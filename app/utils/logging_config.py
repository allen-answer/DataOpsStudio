from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from app.utils.paths import LOGS_DIR


# 兜底脱敏：扫日志消息里 `password=xxx` / `pwd=xxx` / `"password": "xxx"` 之类
# 模式，替换成 `***`。这只是最后一道防线 —— 业务代码不应该主动 log 密码，
# 但如果有人在 traceback / repr(datasource) / debug log 里不小心带上，filter
# 可以兜住，不会写到磁盘 / stdout。
_PASSWORD_PATTERNS = [
    # form / kwargs 风格：password=secret
    re.compile(r"(?i)(password|passwd|pwd)\s*=\s*['\"]?([^'\"\s,)}\]]+)['\"]?"),
    # JSON 风格："password": "secret"
    re.compile(r'(?i)("(?:password|passwd|pwd)"\s*:\s*)"([^"]*)"'),
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
        if msg != original:
            record.msg = msg
            record.args = ()
        return True


def setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / "app.log"

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    redactor = RedactingFilter()

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    file_handler.addFilter(redactor)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(redactor)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)
