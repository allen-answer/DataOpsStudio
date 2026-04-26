from __future__ import annotations

import io
import json
import logging
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dbclients.drivers import detect_drivers
from app.dbclients.factory import fetch_rows, test_connection
from app.lineage.analyzer import analyze_sql_lineage
from app.lineage.batch_analyzer import ScriptInput, analyze_lineage_batch
from app.models import CompareRules, CompareTaskCreate, DataSourceCreate, DatabaseType, RunLimits, SqlMode
from app.services.history import delete_result, list_result_history
from app.services.history_exporter import AVAILABLE_HISTORY_SHEETS, export_history_sheets
from app.services.jobs import cancel_job, get_job, submit_task_run
from app.services.lineage_exporter import write_lineage_batch_excel, write_lineage_excel, write_lineage_json
from app.services.config_io import export_config, import_config
from app.services.repositories import datasource_store, task_store
from app.services.runner import run_task
from app.services.schema_metadata import merge_schema_metadata, parse_schema_metadata
from app.services.sql_tools import sql_assist
from app.utils.sql_guard import validate_readonly_sql
from app.utils.paths import BASE_DIR, RESULTS_DIR


router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
logger = logging.getLogger(__name__)


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    history = list_result_history()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "datasources": datasource_store.list(),
            "tasks": task_store.list(),
            "drivers": detect_drivers(),
            "db_types": [item.value for item in DatabaseType],
            "sql_modes": [item.value for item in SqlMode],
            "history": history[:10],
            "latest_history_by_task": _latest_history_by_task(history),
        },
    )


@router.get("/spa", response_class=HTMLResponse)
def spa_page() -> FileResponse:
    path = BASE_DIR / "static" / "spa" / "index.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="SPA has not been built yet")
    return FileResponse(path)


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request, task_id: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "history": list_result_history(task_id),
            "tasks": task_store.list(),
            "selected_task_id": task_id,
            "history_sheets": AVAILABLE_HISTORY_SHEETS,
        },
    )


@router.get("/lineage", response_class=HTMLResponse)
def lineage_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "lineage.html",
        {"sql": "", "dialect": "", "result": None, "error": None},
    )


@router.get("/lineage/batch", response_class=HTMLResponse)
def lineage_batch_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "lineage_batch.html",
        {"dialect": "", "result": None, "error": None, "exports": None},
    )


@router.post("/lineage/batch", response_class=HTMLResponse)
def lineage_batch_page_analyze(
    request: Request,
    dialect: str = Form(""),
    sql_files: list[UploadFile] = File(default=[]),
    schema_file: list[UploadFile] = File(default=[]),
) -> HTMLResponse:
    try:
        scripts = _lineage_script_inputs(sql_files)
        result = analyze_lineage_batch(scripts, dialect or None, _schema_metadata_files(schema_file))
        exports = _write_lineage_batch_exports(result)
    except Exception as exc:
        logger.exception("lineage batch analyze failed")
        return templates.TemplateResponse(
            request,
            "lineage_batch.html",
            {"dialect": dialect, "result": None, "error": str(exc), "exports": None},
            status_code=400,
        )
    logger.info(
        "lineage batch analyze success files=%s table_edges=%s script_edges=%s warnings=%s dialect=%s",
        result["summary"]["files"],
        result["summary"]["table_edges"],
        result["summary"]["script_edges"],
        result["summary"]["warnings"],
        dialect or "auto",
    )
    return templates.TemplateResponse(
        request,
        "lineage_batch.html",
        {"dialect": dialect, "result": result, "error": None, "exports": exports},
    )


@router.post("/lineage/batch/export")
def lineage_batch_export(
    dialect: str = Form(""),
    sql_files: list[UploadFile] = File(default=[]),
    schema_file: list[UploadFile] = File(default=[]),
    export_format: str = Form("xlsx"),
):
    try:
        scripts = _lineage_script_inputs(sql_files)
        result = analyze_lineage_batch(scripts, dialect or None, _schema_metadata_files(schema_file))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run_id = f"lineage_batch_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    if export_format == "json":
        path = RESULTS_DIR / f"{run_id}.json"
        write_lineage_json(path, result)
    else:
        path = RESULTS_DIR / f"{run_id}.xlsx"
        write_lineage_batch_excel(path, result)
    return FileResponse(path, filename=path.name)


def _write_lineage_batch_exports(result: dict[str, object]) -> dict[str, str]:
    run_id = f"lineage_batch_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    json_path = RESULTS_DIR / f"{run_id}.json"
    excel_path = RESULTS_DIR / f"{run_id}.xlsx"
    write_lineage_json(json_path, result)
    write_lineage_batch_excel(excel_path, result)
    return {"json_filename": json_path.name, "excel_filename": excel_path.name}


@router.post("/lineage", response_class=HTMLResponse)
def lineage_page_analyze(
    request: Request,
    sql: str = Form(""),
    dialect: str = Form(""),
    sql_file: UploadFile | None = File(None),
    schema_file: list[UploadFile] = File(default=[]),
) -> HTMLResponse:
    start = time.perf_counter()
    sql_text = _lineage_sql_text(sql, sql_file)
    try:
        logger.info(
            "lineage analyze start source=%s sql_chars=%s dialect=%s",
            sql_file.filename if sql_file and sql_file.filename else "textarea",
            len(sql_text),
            dialect or "auto",
        )
        result = analyze_sql_lineage(sql_text, dialect or None, _schema_metadata_files(schema_file))
    except Exception as exc:
        logger.exception("lineage analyze failed")
        return templates.TemplateResponse(
            request,
            "lineage.html",
            {"sql": sql_text, "dialect": dialect, "result": None, "error": str(exc)},
            status_code=400,
        )
    logger.info(
        "lineage analyze success source=%s sql_chars=%s tables=%s columns=%s unions=%s elapsed=%.3fs",
        sql_file.filename if sql_file and sql_file.filename else "textarea",
        len(sql_text),
        len(result["tables"]),
        len(result["columns"]),
        len(result["unions"]),
        time.perf_counter() - start,
    )
    return templates.TemplateResponse(
        request,
        "lineage.html",
        {"sql": sql_text, "dialect": dialect, "result": result, "error": None},
    )


@router.post("/api/lineage/analyze")
def lineage_api(payload: dict[str, str] = Body(...)):
    sql = payload.get("sql", "")
    dialect = payload.get("dialect") or None
    if not sql.strip():
        raise HTTPException(status_code=400, detail="sql is required")
    try:
        logger.info("lineage api analyze start sql_chars=%s dialect=%s", len(sql), dialect or "auto")
        schema = parse_schema_metadata(payload.get("schema", "")) if payload.get("schema") else None
        return analyze_sql_lineage(sql, dialect, schema)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/lineage/analyze-form")
def lineage_form_api(
    sql: str = Form(""),
    dialect: str = Form(""),
    sql_file: UploadFile | None = File(None),
    schema_file: list[UploadFile] = File(default=[]),
):
    sql_text = _lineage_sql_text(sql, sql_file)
    try:
        logger.info("lineage form api analyze start sql_chars=%s dialect=%s", len(sql_text), dialect or "auto")
        return analyze_sql_lineage(sql_text, dialect or None, _schema_metadata_files(schema_file))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/lineage/analyze-file")
def lineage_file_api(
    dialect: str = Form(""),
    sql_file: UploadFile = File(...),
    schema_file: list[UploadFile] = File(default=[]),
):
    start = time.perf_counter()
    sql_text = _lineage_sql_text("", sql_file)
    try:
        logger.info(
            "lineage file api analyze start source=%s sql_chars=%s dialect=%s",
            sql_file.filename,
            len(sql_text),
            dialect or "auto",
        )
        result = analyze_sql_lineage(sql_text, dialect or None, _schema_metadata_files(schema_file))
        logger.info(
            "lineage file api analyze success source=%s sql_chars=%s tables=%s columns=%s unions=%s elapsed=%.3fs",
            sql_file.filename,
            len(sql_text),
            len(result["tables"]),
            len(result["columns"]),
            len(result["unions"]),
            time.perf_counter() - start,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/lineage/batch/analyze")
def lineage_batch_api(
    dialect: str = Form(""),
    sql_files: list[UploadFile] = File(default=[]),
    schema_file: list[UploadFile] = File(default=[]),
):
    try:
        scripts = _lineage_script_inputs(sql_files)
        result = analyze_lineage_batch(scripts, dialect or None, _schema_metadata_files(schema_file))
        exports = _write_lineage_batch_exports(result)
        return {"result": result, "exports": exports}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/lineage/export")
def lineage_export(
    sql: str = Form(""),
    dialect: str = Form(""),
    export_format: str = Form("xlsx"),
    schema_file: list[UploadFile] = File(default=[]),
):
    sql_text = _lineage_sql_text(sql, None)
    try:
        result = analyze_sql_lineage(sql_text, dialect or None, _schema_metadata_files(schema_file))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run_id = f"lineage_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    if export_format == "json":
        path = RESULTS_DIR / f"{run_id}.json"
        write_lineage_json(path, result)
    else:
        path = RESULTS_DIR / f"{run_id}.xlsx"
        write_lineage_excel(path, result)
    return FileResponse(path, filename=path.name)


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
    return RedirectResponse(f"/?config_imported=1&datasources={summary['datasources']}&tasks={summary['tasks']}", status_code=303)


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
        "history": history[:50],
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


@router.post("/datasources")
def create_datasource_form(
    name: str = Form(...),
    db_type: DatabaseType = Form(...),
    host: str = Form(...),
    port: int = Form(...),
    database: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
):
    datasource_store.create(
        DataSourceCreate(
            name=name,
            db_type=db_type,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
        )
    )
    return RedirectResponse("/", status_code=303)


@router.post("/datasources/{datasource_id}/edit")
def update_datasource_form(
    datasource_id: str,
    name: str = Form(...),
    db_type: DatabaseType = Form(...),
    host: str = Form(...),
    port: int = Form(...),
    database: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
):
    existing = datasource_store.get(datasource_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Datasource not found")
    datasource_store.update(
        datasource_id,
        DataSourceCreate(
            name=name,
            db_type=db_type,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password or existing.password,
            extra=existing.extra,
        ),
    )
    return RedirectResponse("/", status_code=303)


@router.post("/datasources/{datasource_id}/delete")
def delete_datasource_form(datasource_id: str):
    try:
        datasource_store.delete(datasource_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Datasource not found") from exc
    return RedirectResponse("/", status_code=303)


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


@router.post("/tasks")
def create_task_form(
    name: str = Form(...),
    source_id: str = Form(...),
    target_id: str = Form(...),
    sql_mode: SqlMode = Form(...),
    source_sql: str = Form(...),
    target_sql: str = Form(""),
    key_columns: str = Form(...),
    ignore_columns: str = Form(""),
    column_mappings: str = Form(""),
    numeric_tolerance: str = Form(""),
    trim_strings: bool = Form(False),
    case_insensitive: bool = Form(False),
    empty_as_null: bool = Form(False),
    max_rows: int = Form(100000),
    export_max_rows: int = Form(50000),
    fetch_chunk_size: int = Form(5000),
    stream_compare: bool = Form(False),
):
    keys = [item.strip() for item in key_columns.split(",") if item.strip()]
    payload = CompareTaskCreate(
        name=name,
        source_id=source_id,
        target_id=target_id,
        sql_mode=sql_mode,
        source_sql=source_sql,
        target_sql=target_sql,
        key_columns=keys,
        rules=_parse_rules(
            ignore_columns,
            column_mappings,
            numeric_tolerance,
            trim_strings,
            case_insensitive,
            empty_as_null,
        ),
        limits=RunLimits(
            max_rows=max_rows,
            export_max_rows=export_max_rows,
            fetch_chunk_size=fetch_chunk_size,
            stream_compare=stream_compare,
        ),
    )
    _ensure_datasources(payload.source_id, payload.target_id)
    task_store.create(payload)
    return RedirectResponse("/", status_code=303)


@router.post("/tasks/{task_id}/edit")
def update_task_form(
    task_id: str,
    name: str = Form(...),
    source_id: str = Form(...),
    target_id: str = Form(...),
    sql_mode: SqlMode = Form(...),
    source_sql: str = Form(...),
    target_sql: str = Form(""),
    key_columns: str = Form(...),
    ignore_columns: str = Form(""),
    column_mappings: str = Form(""),
    numeric_tolerance: str = Form(""),
    trim_strings: bool = Form(False),
    case_insensitive: bool = Form(False),
    empty_as_null: bool = Form(False),
    max_rows: int = Form(100000),
    export_max_rows: int = Form(50000),
    fetch_chunk_size: int = Form(5000),
    stream_compare: bool = Form(False),
):
    keys = [item.strip() for item in key_columns.split(",") if item.strip()]
    payload = CompareTaskCreate(
        name=name,
        source_id=source_id,
        target_id=target_id,
        sql_mode=sql_mode,
        source_sql=source_sql,
        target_sql=target_sql,
        key_columns=keys,
        rules=_parse_rules(
            ignore_columns,
            column_mappings,
            numeric_tolerance,
            trim_strings,
            case_insensitive,
            empty_as_null,
        ),
        limits=RunLimits(
            max_rows=max_rows,
            export_max_rows=export_max_rows,
            fetch_chunk_size=fetch_chunk_size,
            stream_compare=stream_compare,
        ),
    )
    _ensure_datasources(payload.source_id, payload.target_id)
    try:
        task_store.update(task_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    return RedirectResponse("/", status_code=303)


@router.post("/tasks/{task_id}/delete")
def delete_task_form(task_id: str):
    try:
        task_store.delete(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    return RedirectResponse("/", status_code=303)


@router.post("/tasks/{task_id}/copy")
def copy_task_form(task_id: str):
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    copied = task_store.create(
        CompareTaskCreate(
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
    )
    return RedirectResponse(f"/?edit_task={copied.id}", status_code=303)


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


@router.post("/history/{run_id}/delete")
def delete_history_page(run_id: str):
    try:
        delete_result(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Result not found") from exc
    return RedirectResponse("/history", status_code=303)


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


@router.post("/tasks/{task_id}/run", response_class=HTMLResponse)
def run_task_page(request: Request, task_id: str):
    try:
        result = run_task(task_id)
    except Exception as exc:
        logger.exception("task page failed task_id=%s", task_id)
        return templates.TemplateResponse(
            request,
            "result.html",
            {"error": str(exc), "result": None},
            status_code=400,
        )
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "result": result,
            "samples_json": json.dumps(result.samples, ensure_ascii=False, indent=2),
            "error": None,
        },
    )


@router.get("/results/{filename}")
def download_result(filename: str):
    path = (RESULTS_DIR / filename).resolve()
    if RESULTS_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="Result not found")
    return FileResponse(path, filename=Path(filename).name)


def _parse_rules(
    ignore_columns: str,
    column_mappings: str,
    numeric_tolerance: str,
    trim_strings: bool,
    case_insensitive: bool,
    empty_as_null: bool,
) -> CompareRules:
    tolerance = float(numeric_tolerance) if numeric_tolerance.strip() else None
    return CompareRules(
        ignore_columns=[item.strip() for item in ignore_columns.split(",") if item.strip()],
        column_mappings=_parse_column_mappings(column_mappings),
        numeric_tolerance=tolerance,
        trim_strings=trim_strings,
        case_insensitive=case_insensitive,
        empty_as_null=empty_as_null,
    )


def _parse_column_mappings(raw: str) -> dict[str, str]:
    mappings: dict[str, str] = {}
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    for item in normalized.replace(",", "\n").split("\n"):
        if not item.strip():
            continue
        separator = _mapping_separator(item)
        if not separator:
            continue
        source, target = item.split(separator, 1)
        source = source.strip()
        target = target.strip()
        if source and target:
            mappings[source] = target
    return mappings


def _mapping_separator(item: str) -> str:
    for separator in ("->", "=>", "→", ":", "："):
        if separator in item:
            return separator
    return ""


def _ensure_datasources(source_id: str, target_id: str) -> None:
    if datasource_store.get(source_id) is None:
        raise HTTPException(status_code=400, detail="source_id does not exist")
    if datasource_store.get(target_id) is None:
        raise HTTPException(status_code=400, detail="target_id does not exist")


def _latest_history_by_task(history: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for item in history:
        task_id = str(item.get("task_id") or "")
        if task_id and task_id not in latest:
            latest[task_id] = item
    return latest


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


def _schema_metadata(schema_file: UploadFile | None) -> dict[str, list[str]] | None:
    if not schema_file or not schema_file.filename:
        return None
    if Path(schema_file.filename).suffix.lower() not in {".json", ".sql", ".txt"}:
        raise HTTPException(status_code=400, detail="Only .json, .sql and .txt schema metadata files are supported")
    try:
        return parse_schema_metadata(_decode_sql_content(schema_file.file.read(), schema_file.filename))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid schema metadata: {exc}") from exc


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


def _schema_metadata_from_zip(content: bytes) -> list[dict[str, list[str]]]:
    items: list[dict[str, list[str]]] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid schema metadata zip file") from exc
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
        raise HTTPException(status_code=400, detail="Schema metadata zip file does not contain .json, .sql or .txt files")
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
