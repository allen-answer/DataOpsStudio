from __future__ import annotations

import json
import logging
import os
import re
from logging.handlers import RotatingFileHandler

from app.utils.paths import LOGS_DIR


# 兜底脱敏：扫日志消息里 `password=xxx` / `pwd=xxx` / `"password": "xxx"` 之类
# 模式，替换成 `***`。这只是最后一道防线 —— 业务代码不应该主动 log 密码，
# 但如果有人在 traceback / repr(datasource) / debug log 里不小心带上，filter
# 可以兜住，不会写到磁盘 / stdout。
_SENSITIVE_KEYS = r"password|passwd|pwd|api_key|apikey|access_token|refresh_token|token|secret|authorization"

_PASSWORD_PATTERNS = [
    # form / kwargs 风格：password=secret
    re.compile(rf"(?i)({_SENSITIVE_KEYS})\s*=\s*['\"]?([^'\"\s,)}}\]]+)['\"]?"),
    # JSON 风格："password": "secret"
    re.compile(rf'(?i)("(?:{_SENSITIVE_KEYS})"\s*:\s*)"([^"]*)"'),
    re.compile(r"(?i)(authorization\s*:\s*)(bearer\s+)?([^,\s)}\]]+)"),
]


class RedactingFilter(logging.Filter):
    """日志记录走 format 之前先脱敏密码字段。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        original = msg
        msg = _PASSWORD_PATTERNS[0].sub(lambda m: f"{m.group(1)}=***", msg)
        msg = _PASSWORD_PATTERNS[1].sub(lambda m: f'{m.group(1)}"***"', msg)
        msg = _PASSWORD_PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2) or ''}***", msg)
        if msg != original:
            record.msg = msg
            record.args = ()
        return True


def _read_request_id() -> str:
    """从 Phase 9 Day 6 ContextVar 拿当前请求的 request_id；无请求上下文返回空。
    用 lazy import 避免 logging_config 在 module-load 时反向依赖 app.api。"""
    try:
        from app.api._error_handler import request_id_ctx
        return request_id_ctx.get() or ""
    except Exception:
        return ""


class RequestIdInjectFilter(logging.Filter):
    """把 request_id 注入到 LogRecord，让 plain / JSON formatter 都能引用。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = _read_request_id()
        return True


class JsonLogFormatter(logging.Formatter):
    """结构化 JSON formatter —— 一行一条 JSON。

    字段：ts / level / logger / msg / request_id（自动从 ContextVar） + extra
    （logging.warning("x", extra={"task_id": "..."}) 这种自定义 kv 透传）。

    出 traceback 时塞进 `exc_info` 字段（多行字符串）。
    """

    _STD_KEYS = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", "") or ""
        if rid:
            payload["request_id"] = rid
        # extra 字段透传（logger.info("x", extra={"task_id": "..."})）
        for k, v in record.__dict__.items():
            if k in self._STD_KEYS or k == "request_id":
                continue
            if k.startswith("_"):
                continue
            try:
                json.dumps(v)
            except (TypeError, ValueError):
                v = str(v)
            payload[k] = v
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _make_formatter(use_json: bool) -> logging.Formatter:
    if use_json:
        return JsonLogFormatter()
    return logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s rid=%(request_id)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / "app.log"

    use_json = os.getenv("DATAOPS_LOG_FORMAT", "plain").strip().lower() == "json"
    formatter = _make_formatter(use_json)

    redactor = RedactingFilter()
    rid_filter = RequestIdInjectFilter()

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    file_handler.addFilter(redactor)
    file_handler.addFilter(rid_filter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(redactor)
    console_handler.addFilter(rid_filter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)
