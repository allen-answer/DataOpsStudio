"""SQL 工作台 v0.4 模板库测试 —— store + API 两层覆盖。

store 层:CRUD / builtin protection / 过滤 / import / export
API 层:鉴权 / endpoint shape / builtin 拒改 403
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import SQLTemplateCreate, SQLTemplateUpdate
from app.sqlide.template_store import (
    BUILTIN_PREFIX, SqlTemplateStore, sql_template_store,
)


# ─── store 单测 ───────────────────────────────────────────────────────


@pytest.fixture
def store_isolated(tmp_path: Path) -> SqlTemplateStore:
    """每个测试一个干净的 store + 一份小型 example(2 个内置)。"""
    example_path = tmp_path / "sql_templates.example.json"
    example_path.write_text(json.dumps([
        {"id": "test-count", "name": "示例计数", "tags": ["示例"], "db_types": ["all"], "sql": "SELECT COUNT(*) FROM t;"},
        {"name": "示例风险", "risk_level": "high", "db_types": ["mysql"], "sql": "DROP TABLE t;"},
    ]), encoding="utf-8")
    return SqlTemplateStore(
        store_path=tmp_path / "sql_templates.json",
        example_path=example_path,
    )


def test_empty_store_returns_only_builtin(store_isolated: SqlTemplateStore):
    items = store_isolated.list()
    assert all(t.builtin for t in items)
    assert {t.id for t in items} == {f"{BUILTIN_PREFIX}test-count", f"{BUILTIN_PREFIX}示例风险"}


def test_create_user_template(store_isolated: SqlTemplateStore):
    payload = SQLTemplateCreate(name="my", sql="SELECT 1")
    t = store_isolated.create(payload, created_by="alice")
    assert t.id and not t.id.startswith(BUILTIN_PREFIX)
    assert t.created_by == "alice"
    assert not t.builtin
    assert t.created_at and t.updated_at


def test_list_combines_user_and_builtin(store_isolated: SqlTemplateStore):
    store_isolated.create(SQLTemplateCreate(name="mine", sql="SELECT 1"), created_by="u")
    items = store_isolated.list()
    user_count = sum(1 for t in items if not t.builtin)
    builtin_count = sum(1 for t in items if t.builtin)
    assert user_count == 1
    assert builtin_count == 2


def test_update_user_template(store_isolated: SqlTemplateStore):
    t = store_isolated.create(SQLTemplateCreate(name="orig", sql="SELECT 1"), created_by="u")
    updated = store_isolated.update(t.id, SQLTemplateUpdate(name="改名", sql="SELECT 2", risk_level="medium"))
    assert updated.name == "改名"
    assert updated.sql == "SELECT 2"
    assert updated.risk_level == "medium"
    assert updated.created_at == t.created_at  # created_at 不动
    assert updated.updated_at != t.updated_at


def test_update_builtin_rejected(store_isolated: SqlTemplateStore):
    with pytest.raises(PermissionError):
        store_isolated.update(
            f"{BUILTIN_PREFIX}test-count",
            SQLTemplateUpdate(name="hack", sql="SELECT 1"),
        )


def test_delete_user_template(store_isolated: SqlTemplateStore):
    t = store_isolated.create(SQLTemplateCreate(name="x", sql="SELECT 1"), created_by="u")
    store_isolated.delete(t.id)
    assert store_isolated.get(t.id) is None


def test_delete_builtin_rejected(store_isolated: SqlTemplateStore):
    with pytest.raises(PermissionError):
        store_isolated.delete(f"{BUILTIN_PREFIX}test-count")


def test_delete_nonexistent_raises(store_isolated: SqlTemplateStore):
    with pytest.raises(KeyError):
        store_isolated.delete("nonexistent")


def test_filter_q_matches_name_description_sql(store_isolated: SqlTemplateStore):
    store_isolated.create(SQLTemplateCreate(name="users 报表", description="月度活跃", sql="SELECT 1"), created_by="u")
    store_isolated.create(SQLTemplateCreate(name="orders", description="trade only", sql="SELECT 2"), created_by="u")
    # name 命中
    assert {t.name for t in store_isolated.list(q="users")} == {"users 报表"}
    # description 命中
    assert {t.name for t in store_isolated.list(q="trade")} == {"orders"}
    # SQL 内容也能搜
    store_isolated.create(SQLTemplateCreate(name="x", sql="SELECT abc FROM y"), created_by="u")
    assert any("abc" in t.sql for t in store_isolated.list(q="abc"))


def test_filter_tags_and_semantics(store_isolated: SqlTemplateStore):
    store_isolated.create(SQLTemplateCreate(name="a", sql="x", tags=["报表", "日报"]), created_by="u")
    store_isolated.create(SQLTemplateCreate(name="b", sql="x", tags=["报表", "周报"]), created_by="u")
    # 单 tag
    assert len(store_isolated.list(tags=["报表"])) == 2
    # AND:必须同时含 报表 + 日报
    res = store_isolated.list(tags=["报表", "日报"])
    assert len(res) == 1
    assert res[0].name == "a"


def test_filter_db_type_includes_all(store_isolated: SqlTemplateStore):
    store_isolated.create(SQLTemplateCreate(name="mysql-only", sql="x", db_types=["mysql"]), created_by="u")
    store_isolated.create(SQLTemplateCreate(name="universal", sql="x", db_types=["all"]), created_by="u")
    res = store_isolated.list(db_type="mysql")
    names = {t.name for t in res}
    assert "mysql-only" in names
    assert "universal" in names  # all 算通用,应该命中


def test_filter_project_id(store_isolated: SqlTemplateStore):
    store_isolated.create(SQLTemplateCreate(name="global", sql="x", project_id=""), created_by="u")
    store_isolated.create(SQLTemplateCreate(name="proj-A", sql="x", project_id="proj-A"), created_by="u")
    # None = 不过滤
    assert len(store_isolated.list(project_id=None)) >= 2
    # "" 只看全局
    res = store_isolated.list(project_id="")
    user_names = {t.name for t in res if not t.builtin}
    assert "global" in user_names
    assert "proj-A" not in user_names
    # 指定 project_id 看 global + 该 project
    res = store_isolated.list(project_id="proj-A")
    user_names = {t.name for t in res if not t.builtin}
    assert "global" in user_names
    assert "proj-A" in user_names


def test_import_creates_new(store_isolated: SqlTemplateStore):
    report = store_isolated.import_templates([
        {"name": "imp1", "sql": "SELECT 1"},
        {"name": "imp2", "sql": "SELECT 2", "tags": ["导入"]},
    ], created_by="u")
    assert report == {"created": 2, "skipped": 0, "errors": 0}


def test_import_skip_duplicates_by_default(store_isolated: SqlTemplateStore):
    store_isolated.create(SQLTemplateCreate(name="同名", sql="orig"), created_by="u")
    report = store_isolated.import_templates([
        {"name": "同名", "sql": "new"},
        {"name": "新的", "sql": "SELECT 1"},
    ], created_by="u")
    assert report["created"] == 1
    assert report["skipped"] == 1
    # 同名模板没被改
    same = [t for t in store_isolated.list() if t.name == "同名"][0]
    assert same.sql == "orig"


def test_import_overwrite_by_name(store_isolated: SqlTemplateStore):
    orig = store_isolated.create(SQLTemplateCreate(name="覆盖", sql="orig"), created_by="u")
    report = store_isolated.import_templates([
        {"name": "覆盖", "sql": "new content"},
    ], created_by="bob", overwrite_by_name=True)
    assert report["created"] == 1
    assert report["skipped"] == 0
    # id 保留(对外引用稳定)
    found = store_isolated.get(orig.id)
    assert found is not None
    assert found.sql == "new content"


def test_import_invalid_payload_counts_errors(store_isolated: SqlTemplateStore):
    report = store_isolated.import_templates([
        {"name": "ok", "sql": "SELECT 1"},
        {"name": "missing sql"},        # 缺 sql 字段
        {"sql": "missing name"},        # 缺 name
    ], created_by="u")
    assert report["created"] == 1
    assert report["errors"] == 2


def test_import_ignores_builtin_flag(store_isolated: SqlTemplateStore):
    """攻击场景:用户尝试通过 import 注入 builtin=true 提权,store 应忽略。"""
    store_isolated.import_templates([
        {"name": "假冒内置", "sql": "DROP TABLE x", "builtin": True},
    ], created_by="u")
    t = next(t for t in store_isolated.list() if t.name == "假冒内置")
    assert t.builtin is False
    assert not t.id.startswith(BUILTIN_PREFIX)


def test_export_excludes_builtin_by_default(store_isolated: SqlTemplateStore):
    store_isolated.create(SQLTemplateCreate(name="mine", sql="x"), created_by="u")
    out = store_isolated.export(include_builtin=False)
    assert len(out) == 1
    assert out[0]["name"] == "mine"


def test_export_can_include_builtin(store_isolated: SqlTemplateStore):
    store_isolated.create(SQLTemplateCreate(name="mine", sql="x"), created_by="u")
    out = store_isolated.export(include_builtin=True)
    names = {e["name"] for e in out}
    assert "mine" in names
    assert "示例计数" in names


# ─── API 集成测试 ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_template_store(isolated_storage, monkeypatch, tmp_path):
    """把全局 sql_template_store 切到 tmp,避免污染真实 config/。
    同时给一份小型 example 让 builtin 行为可测。"""
    example_path = tmp_path / "sql_templates.example.json"
    example_path.write_text(json.dumps([
        {"id": "test-count", "name": "示例计数", "tags": ["示例"], "db_types": ["all"], "sql": "SELECT COUNT(*) FROM t;"},
    ]), encoding="utf-8")
    monkeypatch.setattr(sql_template_store, "_store_path", isolated_storage["cfg"] / "sql_templates.json")
    monkeypatch.setattr(sql_template_store, "_example_path", example_path)


def test_api_anon_blocked(client_anon):
    # viewer+ 才能 GET;anon 必拒
    assert client_anon.get("/api/sql-templates").status_code == 401


def test_api_viewer_can_list(client_viewer):
    r = client_viewer.get("/api/sql-templates")
    assert r.status_code == 200
    data = r.json()
    # 至少有 1 个 builtin example
    assert data["count"] >= 1
    assert any(t.get("builtin") for t in data["items"])


def test_api_viewer_cannot_create(client_viewer):
    r = client_viewer.post("/api/sql-templates", json={"name": "x", "sql": "SELECT 1"})
    assert r.status_code == 403


def test_api_editor_full_crud_cycle(client_editor):
    # create
    r = client_editor.post("/api/sql-templates", json={
        "name": "e2e", "sql": "SELECT 1", "tags": ["test"], "db_types": ["mysql"],
    })
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    assert not r.json()["builtin"]

    # get
    r = client_editor.get(f"/api/sql-templates/{tid}")
    assert r.status_code == 200
    assert r.json()["name"] == "e2e"

    # update
    r = client_editor.put(f"/api/sql-templates/{tid}", json={
        "name": "e2e v2", "sql": "SELECT 2", "tags": ["test"], "db_types": ["mysql"],
    })
    assert r.status_code == 200
    assert r.json()["name"] == "e2e v2"

    # delete
    r = client_editor.delete(f"/api/sql-templates/{tid}")
    assert r.status_code == 200

    # 404 after delete
    r = client_editor.get(f"/api/sql-templates/{tid}")
    assert r.status_code == 404


def test_api_update_builtin_returns_403(client_editor):
    r = client_editor.put("/api/sql-templates/builtin:test-count", json={
        "name": "hack", "sql": "DROP",
    })
    assert r.status_code == 403


def test_api_delete_builtin_returns_403(client_editor):
    r = client_editor.delete("/api/sql-templates/builtin:test-count")
    assert r.status_code == 403


def test_api_search_filter(client_editor):
    client_editor.post("/api/sql-templates", json={"name": "users 报表", "sql": "SELECT 1"})
    client_editor.post("/api/sql-templates", json={"name": "orders 报表", "sql": "SELECT 2"})

    r = client_editor.get("/api/sql-templates?q=users")
    data = r.json()
    names = {t["name"] for t in data["items"]}
    assert "users 报表" in names
    assert "orders 报表" not in names


def test_api_import_export_roundtrip(client_editor):
    # import 2 个
    r = client_editor.post("/api/sql-templates/import", json={
        "templates": [
            {"name": "imp1", "sql": "SELECT 1"},
            {"name": "imp2", "sql": "SELECT 2"},
        ],
    })
    assert r.status_code == 200
    assert r.json()["created"] == 2

    # export(默认不含 builtin)
    r = client_editor.get("/api/sql-templates/export/json")
    out = r.json()
    names = {t["name"] for t in out["templates"]}
    assert "imp1" in names
    assert "imp2" in names
    # builtin 不在
    assert "示例计数" not in names


def test_api_import_overwrite_by_name(client_editor):
    client_editor.post("/api/sql-templates", json={"name": "dup", "sql": "orig"})
    r = client_editor.post("/api/sql-templates/import", json={
        "templates": [{"name": "dup", "sql": "new"}],
        "overwrite_by_name": True,
    })
    assert r.status_code == 200
    assert r.json()["created"] == 1
    # SQL 已被覆盖
    r = client_editor.get("/api/sql-templates?q=dup")
    items = r.json()["items"]
    assert any(t["sql"] == "new" for t in items)
