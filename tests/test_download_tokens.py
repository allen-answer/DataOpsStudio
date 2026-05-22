"""签名下载 token 测试 —— issue/verify + 两个端点（验签 / 后缀白名单 / traversal）。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from jose import jwt

from app.models import CompareTaskCreate, RunLimits
from app.services.download_token import (
    _download_secret,
    issue_download_token,
    verify_download_token,
)


# ─── token 模块（纯函数）─────────────────────────────────────────────────────


def test_issue_verify_roundtrip():
    token, ttl = issue_download_token(
        run_id="r1", relative_path="r1.json", project_id="p1", user_id="u1",
    )
    assert ttl == 300
    payload = verify_download_token(token)
    assert payload is not None
    assert payload["run_id"] == "r1"
    assert payload["rel"] == "r1.json"
    assert payload["project_id"] == "p1"
    assert payload["purpose"] == "download"


def test_verify_rejects_tampered_token():
    token, _ = issue_download_token(
        run_id="r1", relative_path="r1.json", project_id="", user_id="u1",
    )
    assert verify_download_token(token[:-4] + "xxxx") is None


def test_verify_rejects_garbage_and_empty():
    assert verify_download_token("not-a-jwt") is None
    assert verify_download_token("") is None


def test_verify_rejects_wrong_purpose():
    # 用对的密钥签，但 purpose 不是 download —— 防登录 token 拿来当下载 token
    now = datetime.now(timezone.utc)
    fake = jwt.encode(
        {"purpose": "login", "rel": "r1.json",
         "exp": int((now + timedelta(hours=1)).timestamp())},
        _download_secret(), algorithm="HS256",
    )
    assert verify_download_token(fake) is None


def test_verify_rejects_expired():
    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {"purpose": "download", "rel": "r1.json",
         "exp": int((now - timedelta(minutes=1)).timestamp())},
        _download_secret(), algorithm="HS256",
    )
    assert verify_download_token(expired) is None


# ─── GET /api/downloads/{token} ─────────────────────────────────────────────


def test_download_endpoint_serves_signed_file(client, isolated_storage):
    target = isolated_storage["results"] / "demo.json"
    target.write_text('{"hello": "world"}', encoding="utf-8")
    token, _ = issue_download_token(
        run_id="demo", relative_path="demo.json", project_id="", user_id="u1",
    )
    resp = client.get(f"/api/downloads/{token}")
    assert resp.status_code == 200
    assert resp.json() == {"hello": "world"}


def test_download_endpoint_rejects_tampered_token(client, isolated_storage):
    (isolated_storage["results"] / "demo.json").write_text("{}", encoding="utf-8")
    token, _ = issue_download_token(
        run_id="demo", relative_path="demo.json", project_id="", user_id="u1",
    )
    resp = client.get(f"/api/downloads/{token[:-4]}zzzz")
    assert resp.status_code == 401


def test_download_endpoint_rejects_bad_suffix(client, isolated_storage):
    # 即便 token 合法签发，后缀不在白名单 → 400（纵深防御）
    (isolated_storage["results"] / "evil.sh").write_text("rm -rf /", encoding="utf-8")
    token, _ = issue_download_token(
        run_id="x", relative_path="evil.sh", project_id="", user_id="u1",
    )
    resp = client.get(f"/api/downloads/{token}")
    assert resp.status_code == 400


def test_download_endpoint_missing_file_404(client):
    token, _ = issue_download_token(
        run_id="gone", relative_path="gone.json", project_id="", user_id="u1",
    )
    assert client.get(f"/api/downloads/{token}").status_code == 404


def test_download_endpoint_requires_login(client_anon, isolated_storage):
    (isolated_storage["results"] / "demo.json").write_text("{}", encoding="utf-8")
    token, _ = issue_download_token(
        run_id="demo", relative_path="demo.json", project_id="", user_id="u1",
    )
    assert client_anon.get(f"/api/downloads/{token}").status_code == 401


# ─── POST /api/runs/{run_id}/downloads ──────────────────────────────────────


def _seed_legacy_run(results_dir, run_id: str, task_id: str) -> None:
    envelope = {
        "run_id": run_id, "task_id": task_id, "task_name": "t",
        "started_at": "2026-01-01T00:00:00", "elapsed_seconds": 0.1,
        "source_rows": 0, "target_rows": 0,
        "summary": {"only_source": 0, "only_target": 0, "diff": 0, "same": 0},
        "rules": {}, "limits": {}, "schema_report": {},
        "buckets": {"only_source": [], "only_target": [], "diff": [], "same": []},
    }
    (results_dir / f"{run_id}.json").write_text(
        json.dumps(envelope, ensure_ascii=False), encoding="utf-8",
    )


def test_create_download_issues_usable_token(client, isolated_storage):
    from app.services.repositories import task_store
    task = task_store.create(CompareTaskCreate(
        name="t", source_id="ds", target_id="ds", source_sql="select 1",
        key_columns=["id"], limits=RunLimits(),
    ))
    _seed_legacy_run(isolated_storage["results"], "run-001", task.id)

    resp = client.post("/api/runs/run-001/downloads", json={"kind": "result"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["download_url"].startswith("/api/downloads/")
    assert body["expires_in"] == 300
    # 拿签发的 token 真去下
    assert client.get(body["download_url"]).status_code == 200


def test_create_download_bad_kind_400(client, isolated_storage):
    from app.services.repositories import task_store
    task = task_store.create(CompareTaskCreate(
        name="t", source_id="ds", target_id="ds", source_sql="select 1",
        key_columns=["id"], limits=RunLimits(),
    ))
    _seed_legacy_run(isolated_storage["results"], "run-002", task.id)
    resp = client.post("/api/runs/run-002/downloads", json={"kind": "nope"})
    assert resp.status_code == 400


def test_create_download_missing_run_404(client):
    assert client.post("/api/runs/no-such-run/downloads", json={"kind": "result"}).status_code == 404
