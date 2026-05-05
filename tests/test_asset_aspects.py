"""Phase 10 enhancement：asset aspect / classification 测试。

覆盖：
- service CRUD（upsert / list / delete / search_by_type）+ UPSERT 语义
- yaml schema 校验（required / enum / unknown type / unknown asset_kind）
- yaml 缺失时 fallback example
- HTTP 端点 + 鉴权（editor+ 才能写）
- get_table_asset 现在 includes aspects 字段
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import asset_aspects, auth as auth_svc


# ─── service 层：CRUD + 校验 ──────────────────────────────────────────────────


def test_list_aspect_types_returns_example_schema(isolated_storage):
    types = asset_aspects.list_aspect_types()
    type_keys = {t["type"] for t in types}
    # 应包含 example yaml 里所有 type
    assert {"owner", "pii", "sla", "sensitive", "tag", "business_term"}.issubset(type_keys)
    # 每条都带 label / schema / color
    sample = next(t for t in types if t["type"] == "pii")
    assert sample["label"]
    assert sample["color"] == "red"
    assert "level" in sample["schema"]


def test_upsert_aspect_creates_and_lists_back(isolated_storage):
    asset_aspects.upsert_aspect(
        asset_kind="table",
        asset_name="ods.t_users",
        aspect_type="owner",
        value={"username": "alice", "team": "growth"},
        updated_by="admin",
    )
    out = asset_aspects.list_aspects_for_asset("table", "ods.t_users")
    assert len(out) == 1
    assert out[0]["aspect_type"] == "owner"
    assert out[0]["value"]["username"] == "alice"
    assert out[0]["updated_by"] == "admin"


def test_upsert_aspect_replaces_existing_row(isolated_storage):
    """同 (kind,name,type,project) 第二次 upsert 不会复制行，而是覆盖 value。"""
    asset_aspects.upsert_aspect(
        asset_kind="table", asset_name="ods.t_users",
        aspect_type="owner", value={"username": "alice"},
    )
    asset_aspects.upsert_aspect(
        asset_kind="table", asset_name="ods.t_users",
        aspect_type="owner", value={"username": "bob"},
    )
    out = asset_aspects.list_aspects_for_asset("table", "ods.t_users")
    assert len(out) == 1
    assert out[0]["value"]["username"] == "bob"


def test_upsert_aspect_required_field_missing_raises(isolated_storage):
    with pytest.raises(ValueError, match="level"):
        asset_aspects.upsert_aspect(
            asset_kind="table", asset_name="ods.t_users",
            aspect_type="pii", value={"categories": ["phone"]},  # 缺 required level
        )


def test_upsert_aspect_enum_invalid_value_raises(isolated_storage):
    with pytest.raises(ValueError, match="one of"):
        asset_aspects.upsert_aspect(
            asset_kind="table", asset_name="ods.t_users",
            aspect_type="sla", value={"tier": "t9"},  # t9 不在 [t0..t3]
        )


def test_upsert_aspect_unknown_type_raises(isolated_storage):
    with pytest.raises(ValueError, match="unknown aspect_type"):
        asset_aspects.upsert_aspect(
            asset_kind="table", asset_name="ods.t_users",
            aspect_type="not_a_real_type", value={},
        )


def test_upsert_aspect_unknown_asset_kind_raises(isolated_storage):
    with pytest.raises(ValueError, match="asset_kind"):
        asset_aspects.upsert_aspect(
            asset_kind="schema",  # 不在 {table, task, field}
            asset_name="x", aspect_type="owner", value={"username": "a"},
        )


def test_delete_aspect_returns_true_when_hit_false_when_miss(isolated_storage):
    asset_aspects.upsert_aspect(
        asset_kind="table", asset_name="ods.t_users",
        aspect_type="tag", value={"values": ["cdp"]},
    )
    assert asset_aspects.delete_aspect(
        asset_kind="table", asset_name="ods.t_users", aspect_type="tag",
    ) is True
    # 第二次删 = miss
    assert asset_aspects.delete_aspect(
        asset_kind="table", asset_name="ods.t_users", aspect_type="tag",
    ) is False


def test_search_assets_by_aspect_returns_records(isolated_storage):
    asset_aspects.upsert_aspect(
        asset_kind="table", asset_name="ods.t_users",
        aspect_type="pii", value={"level": "high", "categories": ["id_card"]},
    )
    asset_aspects.upsert_aspect(
        asset_kind="table", asset_name="dwd.t_orders",
        aspect_type="pii", value={"level": "low"},
    )
    asset_aspects.upsert_aspect(
        asset_kind="table", asset_name="dim.color",
        aspect_type="tag", value={"values": ["ref"]},
    )
    hits = asset_aspects.search_assets_by_aspect("pii")
    names = {h["asset_name"] for h in hits}
    assert names == {"ods.t_users", "dwd.t_orders"}
    # tag 类型不该出现
    assert all(h["aspect_type"] == "pii" for h in hits)


def test_list_aspects_for_asset_filters_by_project(isolated_storage):
    """传 project_id → 只返回该 project 的 + 全局（project_id=""）。"""
    asset_aspects.upsert_aspect(
        asset_kind="table", asset_name="dwd.shared",
        aspect_type="owner", value={"username": "global-owner"},
        project_id="",  # 全局
    )
    asset_aspects.upsert_aspect(
        asset_kind="table", asset_name="dwd.shared",
        aspect_type="tag", value={"values": ["proj-a-only"]},
        project_id="proj-a",
    )
    asset_aspects.upsert_aspect(
        asset_kind="table", asset_name="dwd.shared",
        aspect_type="sla", value={"tier": "t1"},
        project_id="proj-b",
    )
    in_a = asset_aspects.list_aspects_for_asset("table", "dwd.shared", project_id="proj-a")
    types_in_a = {a["aspect_type"] for a in in_a}
    assert types_in_a == {"owner", "tag"}  # 全局 + proj-a，不含 proj-b
    # 不传 project_id → 三条都返回
    all_aspects = asset_aspects.list_aspects_for_asset("table", "dwd.shared")
    assert len(all_aspects) == 3


# ─── schema 加载兜底 ─────────────────────────────────────────────────────────


def test_load_schema_falls_back_to_example_when_main_missing(isolated_storage, monkeypatch, tmp_path):
    """asset_aspects.yml 不存在时 fallback 到 example —— 这是 dev 默认行为。"""
    # 把 _ASPECT_SCHEMA_FILE 指到一个肯定不存在的临时路径
    fake_main = tmp_path / "nope.yml"
    monkeypatch.setattr(asset_aspects, "_ASPECT_SCHEMA_FILE", fake_main)
    # 清缓存让重新读
    monkeypatch.setattr(asset_aspects, "_schema_cache", {})
    monkeypatch.setattr(asset_aspects, "_schema_mtime", 0.0)

    schema = asset_aspects._load_schema()
    assert "owner" in schema["aspects"]
    assert "pii" in schema["aspects"]


def test_load_schema_broken_yaml_falls_back_to_last_known(isolated_storage, monkeypatch, tmp_path):
    """坏 yaml → log warning + 用上次缓存的 schema，不拖崩。"""
    # 先成功加载一次（拿 example 的 cache）
    monkeypatch.setattr(asset_aspects, "_schema_cache", {})
    monkeypatch.setattr(asset_aspects, "_schema_mtime", 0.0)
    good = asset_aspects._load_schema()
    assert "owner" in good["aspects"]

    # 现在指到坏 yaml
    bad = tmp_path / "asset_aspects.yml"
    bad.write_text("this is not: valid: yaml: ::: [", encoding="utf-8")
    monkeypatch.setattr(asset_aspects, "_ASPECT_SCHEMA_FILE", bad)
    monkeypatch.setattr(asset_aspects, "_schema_mtime", 0.0)  # 强制重读
    # _schema_cache 保留 last known

    after = asset_aspects._load_schema()
    # 仍然能拿到 owner（来自 last known cache）
    assert "owner" in after["aspects"]


# ─── HTTP + 鉴权 ─────────────────────────────────────────────────────────────


@pytest.fixture
def auth_client(isolated_storage):
    """带 admin / editor / viewer 三种用户的 TestClient（从 test_auth.py 复用模式）。"""
    auth_svc.bootstrap_default_admin()
    from app.utils.paths import USERS_FILE
    raw = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    raw.append({
        "id": uuid.uuid4().hex, "username": "alice",
        "password_hash": auth_svc.hash_password("alice123"),
        "role": "editor", "display_name": "Alice",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    raw.append({
        "id": uuid.uuid4().hex, "username": "bob",
        "password_hash": auth_svc.hash_password("bob123"),
        "role": "viewer", "display_name": "Bob",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    USERS_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    auth_svc.user_store.invalidate_cache()

    from main import app
    return TestClient(app)


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_get_aspect_types_endpoint_open(auth_client):
    """读 schema 不要求登录（前端编辑器要拿 schema 渲染表单）。"""
    r = auth_client.get("/api/assets/aspects/types")
    assert r.status_code == 200
    types = {t["type"] for t in r.json()}
    assert "owner" in types and "pii" in types


def test_upsert_aspect_requires_editor_role(auth_client):
    payload = {
        "asset_kind": "table", "asset_name": "ods.t_users",
        "aspect_type": "owner", "value": {"username": "alice"},
    }
    # 未登录 → 401
    assert auth_client.put("/api/assets/aspects", json=payload).status_code == 401
    # viewer → 403
    bob_token = _login(auth_client, "bob", "bob123")
    r = auth_client.put("/api/assets/aspects", json=payload, headers=_bearer(bob_token))
    assert r.status_code == 403
    # editor → 200
    alice_token = _login(auth_client, "alice", "alice123")
    r = auth_client.put("/api/assets/aspects", json=payload, headers=_bearer(alice_token))
    assert r.status_code == 200, r.text
    assert r.json()["updated_by"] == "alice"


def test_upsert_then_table_asset_includes_aspect(auth_client):
    """PUT aspect → GET /api/assets/table/<name> 输出里 aspects 字段含这条。"""
    alice_token = _login(auth_client, "alice", "alice123")
    auth_client.put("/api/assets/aspects", json={
        "asset_kind": "table", "asset_name": "dwd.t_orders",
        "aspect_type": "sla", "value": {"tier": "t0", "refresh_interval": "5m"},
    }, headers=_bearer(alice_token))

    r = auth_client.get("/api/assets/table/dwd.t_orders")
    assert r.status_code == 200
    body = r.json()
    assert "aspects" in body
    assert len(body["aspects"]) == 1
    assert body["aspects"][0]["aspect_type"] == "sla"
    assert body["aspects"][0]["value"]["tier"] == "t0"


def test_upsert_aspect_validation_error_returns_400(auth_client):
    alice_token = _login(auth_client, "alice", "alice123")
    r = auth_client.put("/api/assets/aspects", json={
        "asset_kind": "table", "asset_name": "x",
        "aspect_type": "sla", "value": {"tier": "WRONG_TIER"},
    }, headers=_bearer(alice_token))
    assert r.status_code == 400
    # 错误信息里提到字段名
    assert "tier" in (r.json().get("detail") or "") or "one of" in (r.json().get("detail") or "")


def test_delete_aspect_via_api(auth_client):
    alice_token = _login(auth_client, "alice", "alice123")
    auth_client.put("/api/assets/aspects", json={
        "asset_kind": "table", "asset_name": "ods.t_users",
        "aspect_type": "tag", "value": {"values": ["cdp"]},
    }, headers=_bearer(alice_token))

    r = auth_client.delete(
        "/api/assets/aspects?asset_kind=table&asset_name=ods.t_users&aspect_type=tag",
        headers=_bearer(alice_token),
    )
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    # 第二次 = miss
    r2 = auth_client.delete(
        "/api/assets/aspects?asset_kind=table&asset_name=ods.t_users&aspect_type=tag",
        headers=_bearer(alice_token),
    )
    assert r2.status_code == 200
    assert r2.json()["deleted"] is False


def test_search_aspects_endpoint(auth_client):
    alice_token = _login(auth_client, "alice", "alice123")
    auth_client.put("/api/assets/aspects", json={
        "asset_kind": "table", "asset_name": "ods.t_users",
        "aspect_type": "pii", "value": {"level": "high"},
    }, headers=_bearer(alice_token))
    auth_client.put("/api/assets/aspects", json={
        "asset_kind": "table", "asset_name": "dwd.t_pay",
        "aspect_type": "pii", "value": {"level": "high"},
    }, headers=_bearer(alice_token))

    # search 也开放（admin 反查 PII 资产）
    r = auth_client.get("/api/assets/aspects/search?aspect_type=pii")
    assert r.status_code == 200
    names = {h["asset_name"] for h in r.json()}
    assert names == {"ods.t_users", "dwd.t_pay"}
