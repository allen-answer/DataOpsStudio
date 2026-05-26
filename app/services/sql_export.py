"""SQL 工作台 v0.5+ 结果导出 —— CSV / Excel / JSON / SQL Insert 四种格式。

设计跟 `app/sqlide/runtime.py` 同套路:
- in-memory Export registry + per-export ThreadPoolExecutor
- POST endpoint 短同步 sync_wait;到点未完成返 running + export_id
- 前端 poll status 直到 success/failed → 调 download endpoint 拿文件
- 大结果(几万行)自动走完整异步,小结果一次同步完成

写盘路径:`results/sql_exports/<filename>`
文件名:`<ds_slug>_<title_slug>_<ts>_<id8>.<ext>` —— 保留 datasource + 用户标题 +
时间戳让管理员一目了然(#10)。

跟现有 jobs 模块的关系:
- jobs.py 用 SQLite 持久化 JobInfo,跨重启保留
- 这里 in-memory 是因为导出文件本身在磁盘,registry 丢了 caller 重发即可,
  不上 SQLite 是 KISS(对齐 runtime.py 风格)

NULL / datetime / Decimal / bytes 处理跟 `app/sqlide/executor._serialize_cell`
对齐;**Excel 额外做公式注入防御**(#13):cell 以 `=+-@\t\r\n` 开头时
prepend `'` 让 Excel 当成字符串。
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time as _time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Literal

from app.dbclients.factory import DbClientError, fetch_rows_with_schema
from app.utils.paths import SQL_EXPORTS_DIR
from app.utils.sql_guard import validate_readonly_sql

logger = logging.getLogger(__name__)


ExportFormat = Literal["csv", "excel", "json", "sql"]
SUPPORTED_FORMATS: tuple[ExportFormat, ...] = ("csv", "excel", "json", "sql")

# format → 文件扩展名
_EXT: dict[str, str] = {"csv": "csv", "excel": "xlsx", "json": "json", "sql": "sql"}

# 硬上限:即使 caller 给更大也截,保护磁盘 / 内存(开发期值,可调)
_MAX_ROWS_HARD_CAP = 1_000_000

# 公式注入防御 —— Excel / WPS / Google Sheets 都会把以这些字符开头的 cell 当公式
_FORMULA_PREFIX_PATTERN = re.compile(r"^[=+\-@\t\r]")

# slug 合法字符,其他全转 _,防文件名带路径分隔符 / 特殊字符
_SLUG_RE = re.compile(r"[^A-Za-z0-9_\-]+")

ExportStatus = Literal["pending", "running", "success", "failed", "cancelled"]


@dataclass
class Export:
    id: str
    user_id: str
    datasource_id: str
    format: ExportFormat
    sql: str
    title: str = ""               # 用户给的"标题"(导出文件名一部分,也作为 SQL INSERT 的表名占位)
    max_rows: int = 100_000
    status: ExportStatus = "pending"
    created_at: str = ""
    finished_at: str = ""
    file_path: str = ""           # 绝对路径 —— download endpoint 用
    file_name: str = ""           # 给用户看的文件名(Content-Disposition)
    file_size: int = 0
    row_count: int = 0
    truncated: bool = False
    error: str | None = None

    def to_envelope(self) -> dict[str, Any]:
        return {
            "export_id": self.id,
            "format": self.format,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "error": self.error,
            # 仅当 success 时附 download_url —— 前端按需 fetch
            "download_url": f"/api/sql-workbench/export/{self.id}/download" if self.status == "success" else None,
        }


# ─── registry ────────────────────────────────────────────────────────────

_TTL_SECONDS = 86400  # 导出文件保留 1 天;TTL cleanup 删 registry + 物理文件
_DEFAULT_SYNC_WAIT = 0.5

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sql-export-")
_exports: dict[str, Export] = {}
_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slugify(s: str, *, max_len: int = 40) -> str:
    s = _SLUG_RE.sub("_", (s or "").strip()) or "untitled"
    return s[:max_len]


def _build_file_name(ds_slug: str, title: str, fmt: ExportFormat, export_id: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    title_slug = _slugify(title)
    return f"{_slugify(ds_slug)}_{title_slug}_{ts}_{export_id[:8]}.{_EXT[fmt]}"


def _cleanup_old() -> None:
    """删 finished 且超 TTL 的 export(registry + 文件)。"""
    cutoff_ts = time.time() - _TTL_SECONDS
    with _lock:
        stale: list[str] = []
        for eid, ex in _exports.items():
            if ex.status in ("pending", "running") or not ex.finished_at:
                continue
            try:
                finished_ts = datetime.fromisoformat(ex.finished_at).timestamp()
            except ValueError:
                continue
            if finished_ts < cutoff_ts:
                stale.append(eid)
        for eid in stale:
            ex = _exports.pop(eid, None)
            if ex and ex.file_path:
                try:
                    Path(ex.file_path).unlink(missing_ok=True)
                except OSError:
                    pass


# ─── 序列化 helpers ───────────────────────────────────────────────────────


def _serialize_for_text(value: Any) -> str:
    """文本格式(CSV / SQL)用 —— NULL 返空 / 'NULL' 由 caller 决定。这里
    其他类型转字符串。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)  # 保精度
    if isinstance(value, (datetime, date, _time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def _serialize_for_json(value: Any) -> Any:
    """JSON 用 —— null 保持 None,日期 → iso,Decimal → str(保精度;float 会损失)。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, _time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def _sanitize_for_excel(value: Any) -> Any:
    """Excel 公式注入防御(#13):cell 以 =+-@\\t\\r 开头时 prepend `'`,
    其余类型(数值 / 日期)保持 native 让 openpyxl 写成原生 cell type。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return float(value) if isinstance(value, Decimal) else value
    if isinstance(value, (datetime, date, _time)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    text = str(value)
    if _FORMULA_PREFIX_PATTERN.match(text):
        return "'" + text
    return text


# ─── writers ─────────────────────────────────────────────────────────────


def _write_csv(file_path: Path, columns: list[str], rows: Iterable[list[Any]]) -> tuple[int, int]:
    """写 CSV;返 (row_count, file_size)。
    UTF-8 with BOM 让 Excel 直接双击不乱码。NULL 写空字段。
    """
    count = 0
    with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, dialect="excel", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_serialize_for_text(c) for c in row])
            count += 1
    return count, file_path.stat().st_size


def _write_json(file_path: Path, columns: list[str], rows: Iterable[list[Any]]) -> tuple[int, int]:
    """写 JSON 数组 —— 每行 dict {col: value}。NULL → null,日期 → ISO。"""
    obj_list = []
    count = 0
    for row in rows:
        obj_list.append({col: _serialize_for_json(val) for col, val in zip(columns, row)})
        count += 1
    file_path.write_text(json.dumps(obj_list, ensure_ascii=False, indent=2), encoding="utf-8")
    return count, file_path.stat().st_size


def _write_excel(file_path: Path, columns: list[str], rows: Iterable[list[Any]]) -> tuple[int, int]:
    """走 openpyxl write_only,内存恒定。公式注入防御在 _sanitize_for_excel。"""
    from openpyxl import Workbook
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("data")
    ws.append(columns)
    count = 0
    for row in rows:
        ws.append([_sanitize_for_excel(c) for c in row])
        count += 1
    wb.save(file_path)
    wb.close()
    return count, file_path.stat().st_size


def _write_sql_inserts(file_path: Path, columns: list[str], rows: Iterable[list[Any]], table_name: str) -> tuple[int, int]:
    """写 SQL INSERT 语句;table_name 用用户给的 title slug(再用 _slugify
    保证合法标识符)。每行一句 INSERT,方便 grep / 部分回放。
    NULL 写成 NULL 字面,字符串单引号转义 → ''。"""
    tn = _slugify(table_name) or "data"
    col_list = ", ".join(columns)
    count = 0
    with open(file_path, "w", encoding="utf-8") as f:
        for row in rows:
            values = [_sql_literal(c) for c in row]
            f.write(f"INSERT INTO {tn} ({col_list}) VALUES ({', '.join(values)});\n")
            count += 1
    return count, file_path.stat().st_size


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, _time)):
        return f"'{value.isoformat()}'"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"X'{bytes(value).hex()}'"
    # 字符串 —— 单引号转义 ''
    return "'" + str(value).replace("'", "''") + "'"


_WRITERS = {
    "csv": _write_csv,
    "json": _write_json,
    "excel": _write_excel,
}


# ─── 主入口 ──────────────────────────────────────────────────────────────


def start_export(
    *,
    user_id: str,
    datasource: Any,
    sql: str,
    fmt: ExportFormat,
    title: str = "",
    max_rows: int = 100_000,
    sync_wait: float = _DEFAULT_SYNC_WAIT,
) -> Export:
    """提交一次导出。短同步 sync_wait 后未完成返 pending/running + export_id,
    前端 poll 直到 success;然后通过 download endpoint 拿文件。"""
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported export format: {fmt}")
    max_rows = max(1, min(int(max_rows or 100_000), _MAX_ROWS_HARD_CAP))

    ds_slug = _slugify(getattr(datasource, "name", "") or getattr(datasource, "id", ""))
    eid = uuid.uuid4().hex
    file_name = _build_file_name(ds_slug, title, fmt, eid)
    SQL_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    file_path = SQL_EXPORTS_DIR / file_name

    ex = Export(
        id=eid, user_id=user_id, datasource_id=datasource.id, format=fmt,
        sql=sql, title=title, max_rows=max_rows, status="pending",
        created_at=_now(), file_path=str(file_path), file_name=file_name,
    )
    with _lock:
        _exports[eid] = ex

    def _run() -> None:
        with _lock:
            ex.status = "running"
        try:
            # sql_guard 拦 DML/DDL —— 跟 execute 一个口径
            validate_readonly_sql(sql)
            # 拉数据(多拉 1 行检测 truncated)
            result = fetch_rows_with_schema(
                datasource, sql, max_rows=max_rows + 1, raise_on_overflow=False,
            )
            columns = list(result.columns)
            raw_rows = result.rows
            truncated = len(raw_rows) > max_rows
            if truncated:
                raw_rows = raw_rows[:max_rows]
            # dict-rows → list-rows(按 columns 顺序)
            list_rows = [[r.get(c) for c in columns] for r in raw_rows]

            # 派发到 writer
            if fmt == "sql":
                row_count, file_size = _write_sql_inserts(file_path, columns, list_rows, title or "data")
            else:
                row_count, file_size = _WRITERS[fmt](file_path, columns, list_rows)

            with _lock:
                ex.status = "success"
                ex.row_count = row_count
                ex.file_size = file_size
                ex.truncated = truncated
                ex.finished_at = _now()
        except ValueError as exc:
            # sql_guard 拦截
            with _lock:
                ex.status = "failed"
                ex.error = str(exc)
                ex.finished_at = _now()
        except DbClientError as exc:
            with _lock:
                ex.status = "failed"
                ex.error = str(exc)
                ex.finished_at = _now()
        except Exception as exc:
            logger.exception("sql export worker failed")
            with _lock:
                ex.status = "failed"
                ex.error = f"unexpected: {exc}"
                ex.finished_at = _now()

    _executor.submit(_run)

    # 短同步:快导出 < 500ms 直接返 success 让客户端立即下载
    deadline = time.time() + max(0.0, sync_wait)
    while time.time() < deadline:
        with _lock:
            if ex.status not in ("pending", "running"):
                break
        time.sleep(0.02)

    _cleanup_old()
    return ex


def get_export(export_id: str) -> Export | None:
    with _lock:
        return _exports.get(export_id)


def _reset_for_tests() -> None:
    with _lock:
        _exports.clear()
