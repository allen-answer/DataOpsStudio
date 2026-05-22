"""日志脱敏测试 —— redact_text 各模式 + RedactingFilter 对消息体 / extra 字段。"""
from __future__ import annotations

import logging

from app.utils.logging_config import RedactingFilter, redact_text


def _record(msg: str, **extra) -> logging.LogRecord:
    rec = logging.LogRecord("t", logging.INFO, "", 0, msg, None, None)
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


# ─── redact_text 各模式 ──────────────────────────────────────────────────────


def test_kv_password_redacted():
    assert redact_text("password=secret123") == "password=***"
    assert redact_text("pwd=abc def=keep") == "pwd=*** def=keep"


def test_json_style_token_redacted():
    assert redact_text('{"token": "abc.def"}') == '{"token": "***"}'


def test_authorization_header_redacted():
    assert "***" in redact_text("authorization: bearer eyJa.eyJb.sig")
    assert "eyJ" not in redact_text("authorization: Bearer eyJa.eyJb.sig")


def test_bare_jwt_redacted():
    out = redact_text("decoded eyJhbGciOiJ.eyJzdWIiOiJ.SflKxwRJSM")
    assert "eyJ" not in out
    assert "***" in out


def test_connection_string_password_redacted():
    out = redact_text("dsn mysql://root:mypass@localhost:3306/db")
    assert "mypass" not in out
    assert "root" in out and "localhost" in out  # 只抹口令，不动用户名 / host


def test_non_sensitive_text_untouched():
    text = "task_id=abc123 status=running rows=42"
    assert redact_text(text) == text


def test_empty_text():
    assert redact_text("") == ""


# ─── RedactingFilter：消息体 ─────────────────────────────────────────────────


def test_filter_redacts_message():
    rec = _record("login with password=hunter2")
    RedactingFilter().filter(rec)
    assert "hunter2" not in rec.getMessage()
    assert "***" in rec.getMessage()


def test_filter_keeps_clean_message():
    rec = _record("task started id=t1")
    RedactingFilter().filter(rec)
    assert rec.getMessage() == "task started id=t1"


def test_filter_redacts_message_with_args():
    rec = logging.LogRecord("t", logging.INFO, "", 0, "user %s token=%s", ("bob", "xyz"), None)
    RedactingFilter().filter(rec)
    assert "token=***" in rec.getMessage()


# ─── RedactingFilter：extra 结构化字段 ──────────────────────────────────────


def test_filter_redacts_sensitive_extra_by_name():
    # extra key 名命中敏感名 → 整值替换，不看内容
    rec = _record("ok", token="anything-here", api_key="sk-abcdef")
    RedactingFilter().filter(rec)
    assert rec.token == "***"
    assert rec.api_key == "***"


def test_filter_scrubs_content_of_plain_extra():
    # 普通 extra 名，但值里藏了密钥 → 内容扫描
    rec = _record("ok", note="connect password=leaked123")
    RedactingFilter().filter(rec)
    assert "leaked123" not in rec.note
    assert "***" in rec.note


def test_filter_leaves_non_sensitive_extra():
    rec = _record("ok", task_id="abc123", rows=42)
    RedactingFilter().filter(rec)
    assert rec.task_id == "abc123"
    assert rec.rows == 42


def test_filter_skips_empty_sensitive_extra():
    rec = _record("ok", token="")
    RedactingFilter().filter(rec)
    assert rec.token == ""  # 空值不替换成 ***


def test_filter_always_passes_record():
    # filter 只脱敏，永远放行（返回 True）
    assert RedactingFilter().filter(_record("password=x")) is True
