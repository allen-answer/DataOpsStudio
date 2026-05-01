from __future__ import annotations

import io
import logging
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from app.dbclients.drivers import detect_drivers
from app.dbclients.factory import fetch_rows, test_connection
from app.lineage.analyzer import analyze_sql_lineage
from app.lineage.batch_analyzer import ScriptInput, analyze_lineage_batch
from app.models import CompareTaskCreate, DataSourceCreate, DatabaseType, SqlMode
from app.services.history import delete_result, list_result_history
from app.services.history_exporter import AVAILABLE_HISTORY_SHEETS, export_history_sheets
from app.services.jobs import cancel_job, get_job, submit_task_run
from app.services.lineage_exporter import write_lineage_batch_excel, write_lineage_json
from app.services.config_io import export_config, import_config
from app.services.repositories import datasource_store, task_store
from app.services.runner import run_task
from app.services.schema_metadata import merge_schema_metadata, parse_schema_metadata
from app.services.schema_introspection import fetch_schema_metadata
from app.services.sql_tools import sql_assist
from app.utils.sql_guard import validate_readonly_sql
from app.utils.paths import BASE_DIR, RESULTS_DIR


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/")
def index():
    return RedirectResponse("/spa", status_code=302)


@router.get("/spa")
def spa_page():
    path = BASE_DIR / "static" / "spa" / "index.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="SPA has not been built yet")
    return FileResponse(path)


@router.get("/api/drivers")
def drivers() -> dict[str, dict[str, object]]:
    result = detect_drivers()
    logger.info("driver detection result=%s", result)
    return result


@router.get("/api/bootstrap")
def bootstrap():
    history = list_result_history()
    return {
        "datasources": datasource_store.list(),
        "tasks": task_store.list(),
        "drivers": detect_drivers(),
        "db_types": [item.value for item in DatabaseType],
        "sql_modes": [item.value for item in SqlMode],
        "history": history[:200],
        "history_sheets": AVAILABLE_HISTORY_SHEETS,
    }


@router.get("/api/datasources")
def list_datasources():
    return datasource_store.list()


@router.post("/api/datasources")
def create_datasource(payload: DataSourceCreate):
    return datasource_store.create(payload)


@router.put("/api/datasources/{datasource_id}")
def update_datasource(datasource_id: str, payload: DataSourceCreate):
    try:
        return datasource_store.update(datasource_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Datasource not found") from exc


@router.delete("/api/datasources/{datasource_id}")
def delete_datasource(datasource_id: str):
    try:
        datasource_store.delete(datasource_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Datasource not found") from exc
    return {"ok": True}


@router.post("/api/datasources/{datasource_id}/test")
def test_datasource(datasource_id: str):
    datasource = datasource_store.get(datasource_id)
    if datasource is None:
        raise HTTPException(status_code=404, detail="Datasource not found")
    try:
        return test_connection(datasource)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"连接失败：{exc}") from exc


@router.get("/api/tasks")
def list_tasks():
    return task_store.list()


@router.post("/api/tasks")
def create_task(payload: CompareTaskCreate):
    _ensure_datasources(payload.source_id, payload.target_id)
    return task_store.create(payload)


@router.put("/api/tasks/{task_id}")
def update_task(task_id: str, payload: CompareTaskCreate):
    _ensure_datasources(payload.source_id, payload.target_id)
    try:
        return task_store.update(task_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.delete("/api/tasks/{task_id}")
def delete_task(task_id: str):
    try:
        task_store.delete(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    return {"ok": True}


@router.post("/api/tasks/{task_id}/copy")
def copy_task_api(task_id: str):
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    payload = CompareTaskCreate(
        name=f"{task.name} 副本",
        source_id=task.source_id,
        target_id=task.target_id,
        sql_mode=task.sql_mode,
        source_sql=task.source_sql,
        target_sql=task.target_sql,
        key_columns=list(task.key_columns),
        rules=task.rules,
        limits=task.limits,
    )
    return task_store.create(payload)


@router.post("/api/tasks/{task_id}/run")
def run_task_api(task_id: str):
    try:
        return run_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/tasks/{task_id}/run-async")
def run_task_async_api(task_id: str):
    if task_store.get(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return submit_task_run(task_id)


@router.get("/api/runs/{job_id}")
def run_status_api(job_id: str):
    try:
        return get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc


@router.post("/api/runs/{job_id}/cancel")
def cancel_run_api(job_id: str):
    try:
        return cancel_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc


@router.get("/api/history")
def result_history_api(task_id: str = ""):
    return list_result_history(task_id)


@router.delete("/api/history/{run_id}")
def delete_history_api(run_id: str):
    try:
        delete_result(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Result not found") from exc
    return {"ok": True}


@router.post("/history/export")
def export_history_page(
    run_ids: list[str] = Form(default=[]),
    sheet_names: list[str] = Form(default=[]),
):
    try:
        path = export_history_sheets(run_ids, sheet_names)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, filename=path.name)


@router.post("/api/tasks/{task_id}/preview")
def preview_task_api(task_id: str, payload: dict[str, object] | None = Body(None)):
    payload = payload or {}
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    side = str(payload.get("side") or "source")
    limit = int(payload.get("limit") or 20)
    datasource_id = task.target_id if side == "target" else task.source_id
    override_datasource_id = payload.get("datasource_id")
    if isinstance(override_datasource_id, str) and override_datasource_id.strip():
        datasource_id = override_datasource_id.strip()
    datasource = datasource_store.get(datasource_id)
    if datasource is None:
        raise HTTPException(status_code=404, detail="Datasource not found")
    sql = task.target_sql if side == "target" and task.sql_mode == SqlMode.DOUBLE else task.source_sql
    override_sql = payload.get("sql")
    if isinstance(override_sql, str) and override_sql.strip():
        sql = override_sql
    preview_limit = min(limit, 200)
    try:
        validate_readonly_sql(sql)
        rows = fetch_rows(datasource, sql, max_rows=preview_limit, raise_on_overflow=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"side": side, "limit": preview_limit, "truncated": len(rows) == preview_limit, "rows": rows}


@router.post("/api/sql/assist")
def sql_assist_api(payload: dict[str, str] = Body(...)):
    try:
        return sql_assist(payload.get("sql", ""), payload.get("dialect") or None, payload.get("target_dialect") or None)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/lineage/analyze")
def lineage_api(payload: dict[str, str] = Body(...)):
    sql = payload.get("sql", "")
    dialect = payload.get("dialect") or None
    if not sql.strip():
        raise HTTPException(status_code=400, detail="sql is required")
    try:
        logger.info("lineage api analyze start sql_chars=%s dialect=%s", len(sql), dialect or "auto")
        schema, schema_warnings = _lineage_schema(
            sql,
            parse_schema_metadata(payload.get("schema", "")) if payload.get("schema") else None,
            payload.get("schema_datasource_id") or "",
            payload.get("schema_name") or "",
            payload.get("schema_table_filter") or "",
            _truthy(payload.get("schema_only_sql_tables")),
            payload.get("schema_dialect") or "",
        )
        return _attach_lineage_warnings(analyze_sql_lineage(sql, dialect, schema), schema_warnings)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/lineage/analyze-form")
def lineage_form_api(
    sql: str = Form(""),
    dialect: str = Form(""),
    schema_datasource_id: str = Form(""),
    schema_name: str = Form(""),
    schema_table_filter: str = Form(""),
    schema_only_sql_tables: str = Form(""),
    schema_dialect: str = Form(""),
    sql_file: UploadFile | None = File(None),
    schema_file: list[UploadFile] = File(default=[]),
):
    sql_text = _lineage_sql_text(sql, sql_file)
    try:
        logger.info("lineage form api analyze start sql_chars=%s dialect=%s", len(sql_text), dialect or "auto")
        schema, schema_warnings = _lineage_schema(
            sql_text,
            _schema_metadata_files(schema_file),
            schema_datasource_id,
            schema_name,
            schema_table_filter,
            _truthy(schema_only_sql_tables),
            schema_dialect,
        )
        return _attach_lineage_warnings(analyze_sql_lineage(sql_text, dialect or None, schema), schema_warnings)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.post("/api/lineage/batch/analyze")
def lineage_batch_api(
    dialect: str = Form(""),
    schema_datasource_id: str = Form(""),
    schema_name: str = Form(""),
    schema_table_filter: str = Form(""),
    schema_only_sql_tables: str = Form(""),
    schema_dialect: str = Form(""),
    sql_files: list[UploadFile] = File(default=[]),
    schema_file: list[UploadFile] = File(default=[]),
):
    try:
        scripts = _lineage_script_inputs(sql_files)
        combined_sql = "\n;\n".join(script.sql for script in scripts)
        schema, schema_warnings = _lineage_schema(
            combined_sql,
            _schema_metadata_files(schema_file),
            schema_datasource_id,
            schema_name,
            schema_table_filter,
            _truthy(schema_only_sql_tables),
            schema_dialect,
        )
        result = analyze_lineage_batch(
            scripts,
            dialect or None,
            schema,
        )
        result["warnings"] = schema_warnings + result.get("warnings", [])
        if "summary" in result:
            result["summary"]["warnings"] = len(result["warnings"])
        exports = _write_lineage_batch_exports(result)
        return {"result": result, "exports": exports}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/config/export")
def config_export():
    path = export_config()
    return FileResponse(path, filename=path.name)


@router.post("/config/import")
def config_import(config_file: UploadFile = File(...)):
    if not config_file.filename or Path(config_file.filename).suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="Only .json config files are supported")
    try:
        summary = import_config(config_file.file.read())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        f"/spa?config_imported=1&datasources={summary['datasources']}&tasks={summary['tasks']}",
        status_code=303,
    )


@router.get("/results/{filename}")
def download_result(filename: str):
    path = (RESULTS_DIR / filename).resolve()
    if RESULTS_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="Result not found")
    return FileResponse(path, filename=Path(filename).name)


def _write_lineage_batch_exports(result: dict[str, object]) -> dict[str, str]:
    run_id = f"lineage_batch_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    json_path = RESULTS_DIR / f"{run_id}.json"
    excel_path = RESULTS_DIR / f"{run_id}.xlsx"
    write_lineage_json(json_path, result)
    write_lineage_batch_excel(excel_path, result)
    return {"json_filename": json_path.name, "excel_filename": excel_path.name}


def _ensure_datasources(source_id: str, target_id: str) -> None:
    if datasource_store.get(source_id) is None:
        raise HTTPException(status_code=400, detail="source_id does not exist")
    if datasource_store.get(target_id) is None:
        raise HTTPException(status_code=400, detail="target_id does not exist")


def _lineage_sql_text(sql: str, sql_file: UploadFile | None) -> str:
    if sql_file and sql_file.filename:
        suffix = Path(sql_file.filename).suffix.lower()
        if suffix not in {".sql", ".txt"}:
            raise HTTPException(status_code=400, detail="Only .sql and .txt files are supported")
        content = sql_file.file.read()
        for encoding in ("utf-8-sig", "utf-8", "gbk"):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise HTTPException(status_code=400, detail="SQL file encoding must be UTF-8 or GBK")
        if not text.strip():
            raise HTTPException(status_code=400, detail="SQL file is empty")
        return text

    if not sql.strip():
        raise HTTPException(status_code=400, detail="sql is required")
    return sql


def _schema_metadata_files(schema_files: list[UploadFile]) -> dict[str, list[str]] | None:
    items: list[dict[str, list[str]]] = []
    for upload in schema_files:
        if not upload or not upload.filename:
            continue
        filename = Path(upload.filename).name
        suffix = Path(filename).suffix.lower()
        content = upload.file.read()
        if suffix == ".zip":
            items.extend(_schema_metadata_from_zip(content))
            continue
        if suffix not in {".json", ".sql", ".txt"}:
            raise HTTPException(
                status_code=400,
                detail=f"Only .json, .sql, .txt and .zip schema metadata files are supported: {filename}",
            )
        try:
            items.append(parse_schema_metadata(_decode_sql_content(content, filename)))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid schema metadata {filename}: {exc}") from exc
    merged = merge_schema_metadata(*items)
    return merged or None


def _lineage_schema(
    sql: str,
    file_schema: dict[str, list[str]] | None,
    datasource_id: str,
    schema_name: str,
    table_filter: str,
    only_sql_tables: bool,
    schema_dialect: str,
) -> tuple[dict[str, list[str]] | None, list[dict[str, str]]]:
    datasource_schema, warnings = _schema_metadata_from_datasource(
        datasource_id,
        schema_name,
        table_filter,
        _schema_table_candidates(sql) if only_sql_tables else [],
        schema_dialect,
    )
    return _merge_lineage_schema(file_schema, datasource_schema), warnings


def _schema_metadata_from_datasource(
    datasource_id: str,
    schema_name: str = "",
    table_filter: str = "",
    include_tables: list[str] | None = None,
    schema_dialect: str = "",
) -> tuple[dict[str, list[str]] | None, list[dict[str, str]]]:
    if not datasource_id.strip():
        return None, []
    datasource = datasource_store.get(datasource_id.strip())
    if datasource is None:
        return None, [{"type": "Schema 拉取失败", "message": "指定的 Schema 数据源不存在"}]
    try:
        schema = fetch_schema_metadata(
            datasource,
            schema_name=schema_name,
            table_filter=table_filter,
            include_tables=include_tables or [],
            schema_dialect=schema_dialect,
        )
    except Exception as exc:
        return None, [{"type": "Schema 拉取失败", "message": str(exc)}]
    if not schema:
        return None, [{"type": "Schema 为空", "message": "数据源未返回字段元数据，请检查 schema/database、表名过滤或查询权限"}]
    return schema, [{"type": "Schema 自动拉取", "message": f"已从数据源拉取 {len(schema)} 个表标识的字段元数据"}]


def _merge_lineage_schema(*items: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
    merged = merge_schema_metadata(*items)
    return merged or None


def _attach_lineage_warnings(result: dict[str, object], warnings: list[dict[str, str]]) -> dict[str, object]:
    if warnings:
        result["warnings"] = warnings + list(result.get("warnings", []))
    return result


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _schema_table_candidates(sql: str) -> list[str]:
    pattern = re.compile(
        r"\b(?:from|join|into|update|merge\s+into|using|table)\s+([\"`\[\]\w.$#]+)",
        flags=re.IGNORECASE,
    )
    result: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(sql):
        table = match.group(1).strip().strip('"`[]')
        key = table.lower()
        if table and key not in seen and not table.startswith("@"):
            seen.add(key)
            result.append(table)
    return result


_ZIP_MAX_FILES = 500
_ZIP_MAX_DECOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MB


def _check_zip_safety(archive: zipfile.ZipFile) -> None:
    entries = [e for e in archive.infolist() if not e.is_dir()]
    if len(entries) > _ZIP_MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Zip file contains too many files (max {_ZIP_MAX_FILES})")
    total = sum(e.file_size for e in entries)
    if total > _ZIP_MAX_DECOMPRESSED_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Zip file decompressed size exceeds limit ({_ZIP_MAX_DECOMPRESSED_BYTES // 1024 // 1024} MB)",
        )


def _schema_metadata_from_zip(content: bytes) -> list[dict[str, list[str]]]:
    items: list[dict[str, list[str]]] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid schema metadata zip file") from exc
    _check_zip_safety(archive)
    for item in archive.infolist():
        if item.is_dir():
            continue
        suffix = Path(item.filename).suffix.lower()
        if suffix not in {".json", ".sql", ".txt"}:
            continue
        name = item.filename.replace("\\", "/")
        try:
            items.append(parse_schema_metadata(_decode_sql_content(archive.read(item), name)))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid schema metadata {name}: {exc}") from exc
    if not items:
        raise HTTPException(
            status_code=400,
            detail="Schema metadata zip file does not contain .json, .sql or .txt files",
        )
    return items


def _lineage_script_inputs(sql_files: list[UploadFile]) -> list[ScriptInput]:
    scripts: list[ScriptInput] = []
    for upload in sql_files:
        if not upload.filename:
            continue
        filename = Path(upload.filename).name
        suffix = Path(filename).suffix.lower()
        content = upload.file.read()
        if suffix == ".zip":
            scripts.extend(_scripts_from_zip(content))
            continue
        if suffix not in {".sql", ".txt"}:
            raise HTTPException(status_code=400, detail=f"Only .sql, .txt and .zip files are supported: {filename}")
        scripts.append(ScriptInput(file_name=filename, sql=_decode_sql_content(content, filename)))

    if not scripts:
        raise HTTPException(status_code=400, detail="Please upload at least one .sql, .txt or .zip file")
    return scripts


def _scripts_from_zip(content: bytes) -> list[ScriptInput]:
    scripts: list[ScriptInput] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file") from exc
    _check_zip_safety(archive)
    for item in archive.infolist():
        if item.is_dir():
            continue
        suffix = Path(item.filename).suffix.lower()
        if suffix not in {".sql", ".txt"}:
            continue
        name = item.filename.replace("\\", "/")
        scripts.append(ScriptInput(file_name=name, sql=_decode_sql_content(archive.read(item), name)))
    if not scripts:
        raise HTTPException(status_code=400, detail="Zip file does not contain .sql or .txt files")
    return scripts


def _decode_sql_content(content: bytes, filename: str) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(status_code=400, detail=f"{filename} encoding must be UTF-8 or GBK")
    if not text.strip():
        raise HTTPException(status_code=400, detail=f"{filename} is empty")
    return text
