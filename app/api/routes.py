from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from app.dbclients.drivers import detect_drivers
from app.dbclients.factory import fetch_columns, fetch_rows, test_connection
from app.models import CompareTaskCreate, DataSourceCreate, DatabaseType, SourceKind, SqlMode, WorkflowCreate
from app.readers.excel_reader import list_columns as read_excel_columns
from app.services import excel_uploads, lineage_service
from app.services.history import delete_result, list_result_history
from app.services.history_exporter import AVAILABLE_HISTORY_SHEETS, export_history_sheets
from app.services.jobs import cancel_job, get_job, submit_task_run, submit_workflow_run
from app.services.config_io import export_config, import_config
from app.services.repositories import datasource_store, task_store, workflow_store
from app.services.runner import run_task
from app.services.sql_tools import sql_assist
from app.services.workflow_engine import run_workflow, topological_order
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
        "workflows": workflow_store.list(),
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
    _ensure_datasources_for_kind(payload)
    return task_store.create(payload)


@router.put("/api/tasks/{task_id}")
def update_task(task_id: str, payload: CompareTaskCreate):
    _ensure_datasources_for_kind(payload)
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


@router.get("/api/workflows")
def list_workflows():
    return workflow_store.list()


@router.post("/api/workflows")
def create_workflow(payload: WorkflowCreate):
    _ensure_workflow_node_targets(payload)
    return workflow_store.create(payload)


@router.put("/api/workflows/{workflow_id}")
def update_workflow(workflow_id: str, payload: WorkflowCreate):
    _ensure_workflow_node_targets(payload)
    try:
        return workflow_store.update(workflow_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc


@router.delete("/api/workflows/{workflow_id}")
def delete_workflow(workflow_id: str):
    try:
        workflow_store.delete(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc
    return {"ok": True}


@router.post("/api/workflows/{workflow_id}/run")
def run_workflow_api(workflow_id: str, payload: dict[str, object] | None = Body(None)):
    workflow = workflow_store.get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    variables = _coerce_string_dict((payload or {}).get("variables"))
    try:
        run = run_workflow(workflow, variables)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return run.model_dump(mode="json")


@router.post("/api/workflows/{workflow_id}/run-async")
def run_workflow_async_api(workflow_id: str, payload: dict[str, object] | None = Body(None)):
    if workflow_store.get(workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    variables = _coerce_string_dict((payload or {}).get("variables"))
    return submit_workflow_run(workflow_id, variables)


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


@router.post("/api/preview/columns")
def preview_columns_api(payload: dict[str, object] = Body(...)):
    """Return column names for a SQL query or Excel sheet without persisting
    a task. Used by the field-selection UI so users can pick include/exclude
    before saving the task."""
    kind = str(payload.get("kind") or "sql").lower()
    if kind == "sql":
        datasource_id = str(payload.get("datasource_id") or "").strip()
        sql = str(payload.get("sql") or "")
        datasource = datasource_store.get(datasource_id)
        if datasource is None:
            raise HTTPException(status_code=404, detail="Datasource not found")
        try:
            validate_readonly_sql(sql)
            columns = fetch_columns(datasource, sql)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"columns": columns}
    if kind == "excel":
        excel_path = str(payload.get("excel_path") or "")
        sheet = str(payload.get("sheet") or "")
        header_row = int(payload.get("header_row") or 1)
        try:
            absolute_path = excel_uploads.resolve_excel_path(excel_path)
            columns = read_excel_columns(absolute_path, sheet=sheet, header_row=header_row)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"columns": columns}
    raise HTTPException(status_code=400, detail=f"Unknown kind: {kind}")


@router.post("/api/sql/assist")
def sql_assist_api(payload: dict[str, str] = Body(...)):
    try:
        return sql_assist(payload.get("sql", ""), payload.get("dialect") or None, payload.get("target_dialect") or None)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/uploads/excel")
def upload_excel_api(file: UploadFile = File(...)):
    return excel_uploads.save_uploaded_excel(file)


@router.post("/api/lineage/analyze")
def lineage_api(payload: dict[str, str] = Body(...)):
    try:
        return lineage_service.analyze_json(payload)
    except HTTPException:
        raise
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
    try:
        return lineage_service.analyze_form(
            sql, dialect, schema_datasource_id, schema_name,
            schema_table_filter, schema_only_sql_tables, schema_dialect,
            sql_file, schema_file,
        )
    except HTTPException:
        raise
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
        return lineage_service.analyze_batch(
            dialect, schema_datasource_id, schema_name,
            schema_table_filter, schema_only_sql_tables, schema_dialect,
            sql_files, schema_file,
        )
    except HTTPException:
        raise
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


def _ensure_datasources_for_kind(payload: CompareTaskCreate) -> None:
    """Datasource existence only matters for SQL-kind sides; Excel sides
    persist file paths and don't need a registered datasource."""
    if payload.source_kind == SourceKind.SQL and datasource_store.get(payload.source_id) is None:
        raise HTTPException(status_code=400, detail="source_id does not exist")
    if payload.target_kind == SourceKind.SQL and datasource_store.get(payload.target_id) is None:
        raise HTTPException(status_code=400, detail="target_id does not exist")


def _ensure_workflow_node_targets(payload: WorkflowCreate) -> None:
    """Validate per-type config + the DAG is well-formed. Catching this at
    create time gives a clear 400 instead of a confusing failure mid-run."""
    for node in payload.nodes:
        kind = node.type.value
        if kind == "compare":
            task_id = str(node.config.get("task_id") or "").strip()
            if not task_id:
                raise HTTPException(status_code=400, detail=f"node {node.id}: compare requires config.task_id")
            if task_store.get(task_id) is None:
                raise HTTPException(status_code=400, detail=f"node {node.id}: task {task_id} does not exist")
        elif kind == "lineage":
            if not str(node.config.get("sql") or "").strip():
                raise HTTPException(status_code=400, detail=f"node {node.id}: lineage requires config.sql")
        elif kind == "http":
            url = str(node.config.get("url") or "").strip()
            if not url:
                raise HTTPException(status_code=400, detail=f"node {node.id}: http requires config.url")
            if not (url.startswith("http://") or url.startswith("https://")):
                raise HTTPException(status_code=400, detail=f"node {node.id}: http url must start with http:// or https://")
    try:
        topological_order(payload.nodes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _coerce_string_dict(value: object | None) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}
