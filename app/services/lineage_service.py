from __future__ import annotations

import io
import logging
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.lineage.analyzer import analyze_sql_lineage
from app.lineage.batch_analyzer import ScriptInput, analyze_lineage_batch
from app.services._io_utils import check_zip_safety, decode_sql_content, truthy
from app.services import excel_uploads
from app.services.lineage_ai import enrich_lineage_result, enqueue_lineage_ai_enrichment
from app.services.lineage_ai import lineage_ai_status
from app.services.lineage_exporter import write_lineage_batch_excel, write_lineage_json
from app.services.schema_metadata import parse_schema_metadata
from app.services.schema_service import resolve_lineage_schema, schema_from_files
from app.utils.paths import RESULTS_DIR

logger = logging.getLogger(__name__)


def ai_status() -> dict[str, object]:
    return lineage_ai_status()


def analyze_json(payload: dict[str, str], *, ai_async: bool = True) -> dict[str, object]:
    sql = payload.get("sql", "")
    dialect = payload.get("dialect") or None
    if not sql.strip():
        raise HTTPException(status_code=400, detail="sql is required")
    logger.info("lineage api analyze start sql_chars=%s dialect=%s", len(sql), dialect or "auto")
    file_schema = parse_schema_metadata(payload.get("schema", "")) if payload.get("schema") else None
    schema, schema_warnings = resolve_lineage_schema(
        sql,
        file_schema,
        payload.get("schema_datasource_id") or "",
        payload.get("schema_name") or "",
        payload.get("schema_table_filter") or "",
        truthy(payload.get("schema_only_sql_tables")),
        payload.get("schema_dialect") or "",
    )
    result = _attach_warnings(analyze_sql_lineage(sql, dialect, schema), schema_warnings)
    return _attach_ai_enrichment(
        result,
        sql_text=sql,
        dialect=dialect,
        scope="single",
        enabled=truthy(payload.get("ai_enabled")),
        ai_async=ai_async,
    )


def analyze_form(
    sql: str,
    dialect: str,
    schema_datasource_id: str,
    schema_name: str,
    schema_table_filter: str,
    schema_only_sql_tables: str,
    schema_dialect: str,
    sql_file: UploadFile | None,
    schema_file: list[UploadFile],
    ai_enabled: str = "",
    ai_async: bool = True,
) -> dict[str, object]:
    sql_text = _sql_text(sql, sql_file)
    logger.info("lineage form api analyze start sql_chars=%s dialect=%s", len(sql_text), dialect or "auto")
    schema, schema_warnings = resolve_lineage_schema(
        sql_text,
        schema_from_files(schema_file),
        schema_datasource_id,
        schema_name,
        schema_table_filter,
        truthy(schema_only_sql_tables),
        schema_dialect,
    )
    result = _attach_warnings(analyze_sql_lineage(sql_text, dialect or None, schema), schema_warnings)
    return _attach_ai_enrichment(
        result,
        sql_text=sql_text,
        dialect=dialect or None,
        scope="single",
        enabled=truthy(ai_enabled),
        ai_async=ai_async,
    )


def analyze_batch(
    dialect: str,
    schema_datasource_id: str,
    schema_name: str,
    schema_table_filter: str,
    schema_only_sql_tables: str,
    schema_dialect: str,
    sql_files: list[UploadFile],
    schema_file: list[UploadFile],
    ai_enabled: str = "",
    ai_async: bool = True,
) -> dict[str, object]:
    scripts = _script_inputs(sql_files)
    result = _analyze_scripts(
        scripts,
        dialect=dialect,
        schema_datasource_id=schema_datasource_id,
        schema_name=schema_name,
        schema_table_filter=schema_table_filter,
        schema_only_sql_tables=schema_only_sql_tables,
        schema_dialect=schema_dialect,
        schema_file_schema=schema_from_files(schema_file),
        ai_enabled=ai_enabled,
        ai_async=ai_async,
    )
    exports = _write_batch_exports(result)
    return {"result": result, "exports": exports}


def analyze_stored_script(payload: dict[str, str], *, ai_async: bool = True) -> dict[str, object]:
    """Analyze a workflow lineage node that references an uploaded file.

    `payload["script_path"]` is a path previously returned by
    /api/uploads/lineage-script. SQL/TXT files use the single-script shape;
    ZIP files use the batch shape, but still return the result at the top
    level so downstream workflow nodes can consume datasets directly.
    """
    script_path = str(payload.get("script_path") or payload.get("file_path") or "").strip()
    if not script_path:
        raise HTTPException(status_code=400, detail="script_path is required")
    path = excel_uploads.resolve_uploaded_path(
        script_path,
        allowed_suffixes={".sql", ".txt", ".zip"},
        label="Lineage script file",
    )
    filename = str(payload.get("script_filename") or path.name)
    suffix = path.suffix.lower()
    if suffix == ".zip":
        scripts = _scripts_from_zip(path.read_bytes())
        result = _analyze_scripts(
            scripts,
            dialect=payload.get("dialect") or "",
            schema_datasource_id=payload.get("schema_datasource_id") or "",
            schema_name=payload.get("schema_name") or "",
            schema_table_filter=payload.get("schema_table_filter") or "",
            schema_only_sql_tables=payload.get("schema_only_sql_tables") or "",
            schema_dialect=payload.get("schema_dialect") or "",
            schema_file_schema=parse_schema_metadata(payload.get("schema", "")) if payload.get("schema") else None,
            ai_enabled=payload.get("ai_enabled") or "",
            ai_async=ai_async,
        )
        result["input_mode"] = "uploaded_zip"
        result["script_path"] = script_path
        return result

    sql_text = decode_sql_content(path.read_bytes(), filename)
    result = analyze_json({**payload, "sql": sql_text}, ai_async=ai_async)
    result["input_mode"] = "uploaded_file"
    result["script_path"] = script_path
    result["script_filename"] = filename
    return result


def _analyze_scripts(
    scripts: list[ScriptInput],
    *,
    dialect: str,
    schema_datasource_id: str,
    schema_name: str,
    schema_table_filter: str,
    schema_only_sql_tables: str,
    schema_dialect: str,
    schema_file_schema: dict[str, list[str]] | None,
    ai_enabled: str = "",
    ai_async: bool = True,
) -> dict[str, object]:
    combined_sql = "\n;\n".join(script.sql for script in scripts)
    schema, schema_warnings = resolve_lineage_schema(
        combined_sql,
        schema_file_schema,
        schema_datasource_id,
        schema_name,
        schema_table_filter,
        truthy(schema_only_sql_tables),
        schema_dialect,
    )
    result = analyze_lineage_batch(scripts, dialect or None, schema)
    result["warnings"] = schema_warnings + result.get("warnings", [])
    if "summary" in result:
        result["summary"]["warnings"] = len(result["warnings"])
    _attach_ai_enrichment(
        result,
        sql_text=combined_sql,
        dialect=dialect or None,
        scope="batch",
        scripts=[{"file_name": script.file_name, "sql": script.sql} for script in scripts],
        enabled=truthy(ai_enabled),
        ai_async=ai_async,
    )
    return result


def _attach_ai_enrichment(
    result: dict[str, object],
    *,
    sql_text: str,
    dialect: str | None,
    scope: str,
    enabled: bool,
    scripts: list[dict[str, str]] | None = None,
    ai_async: bool = True,
) -> dict[str, object]:
    # Phase 7 双轨 A：AI 解析失败兜底 —— 先做（同步、快速、独立字段），再做 enrichment
    # 兜底针对 result["parse_errors"]：调 AI 推断 source/target 表，输出 result["ai_inferred"]
    _attach_ai_inference(result, dialect=dialect, enabled=enabled)
    attach = enqueue_lineage_ai_enrichment if ai_async else enrich_lineage_result
    return attach(
        result,
        sql_text=sql_text,
        dialect=dialect,
        scope=scope,
        scripts=scripts or [],
        enabled=enabled,
    )


def _attach_ai_inference(
    result: dict[str, object],
    *,
    dialect: str | None,
    enabled: bool,
) -> None:
    """对 result.parse_errors 调 AI 兜底推断（同步、best-effort、不阻塞主流程）。

    结果落 result["ai_inferred"]：edges/columns 列表 + warnings + trigger_count 等。
    要求 LineageAIConfig.enable_inference=True 才执行；任一失败默默降级，不抛错。
    """
    parse_errors = result.get("parse_errors") or []
    if not enabled or not parse_errors:
        return
    try:
        # lazy import 避免 lineage_service ↔ lineage_ai_inference 循环
        from app.services.lineage_ai import _config as _ai_config
        from app.services.lineage_ai_inference import infer_from_parse_errors

        config = _ai_config()
        if not getattr(config, "enable_inference", False):
            return
        # 白名单：从已识别的 tables / columns 取（这些是规则解析的事实，安全）
        tables_field = result.get("tables") or []
        table_names: set[str] = set()
        for t in tables_field:
            if isinstance(t, dict):
                name = t.get("name") or t.get("table") or ""
                if name:
                    table_names.add(name)
            elif isinstance(t, str) and t:
                table_names.add(t)
        column_names: set[str] = set()
        for col in result.get("columns") or []:
            if isinstance(col, dict):
                name = col.get("output_column") or ""
                if name:
                    column_names.add(name)
        for mapping in result.get("insert_mappings") or []:
            if isinstance(mapping, dict):
                for sc in mapping.get("source_columns") or []:
                    if isinstance(sc, str):
                        column_names.add(sc.split(".")[-1])
                for tc in mapping.get("target_columns") or []:
                    if isinstance(tc, str):
                        column_names.add(tc)

        inferred = infer_from_parse_errors(
            parse_errors,
            table_whitelist=table_names,
            column_whitelist=column_names,
            dialect=dialect or "",
            config=config,
            max_fragment_chars=config.inference_max_fragment_chars,
            max_fragments=config.inference_max_fragments,
        )
        result["ai_inferred"] = inferred
    except Exception as exc:
        logger.warning("AI inference fallback failed: %s", exc)
        result["ai_inferred"] = {
            "edges": [],
            "warnings": [{"type": "ai_inference_pipeline_error", "message": str(exc)}],
            "trigger_count": 0,
            "filtered_count": 0,
        }


def _sql_text(sql: str, sql_file: UploadFile | None) -> str:
    if sql_file and sql_file.filename:
        suffix = Path(sql_file.filename).suffix.lower()
        if suffix not in {".sql", ".txt"}:
            raise HTTPException(status_code=400, detail="Only .sql and .txt files are supported")
        return decode_sql_content(sql_file.file.read(), sql_file.filename)
    if not sql.strip():
        raise HTTPException(status_code=400, detail="sql is required")
    return sql


def _script_inputs(sql_files: list[UploadFile]) -> list[ScriptInput]:
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
        scripts.append(ScriptInput(file_name=filename, sql=decode_sql_content(content, filename)))

    if not scripts:
        raise HTTPException(status_code=400, detail="Please upload at least one .sql, .txt or .zip file")
    return scripts


def _scripts_from_zip(content: bytes) -> list[ScriptInput]:
    scripts: list[ScriptInput] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file") from exc
    check_zip_safety(archive)
    for item in archive.infolist():
        if item.is_dir():
            continue
        suffix = Path(item.filename).suffix.lower()
        if suffix not in {".sql", ".txt"}:
            continue
        name = item.filename.replace("\\", "/")
        scripts.append(ScriptInput(file_name=name, sql=decode_sql_content(archive.read(item), name)))
    if not scripts:
        raise HTTPException(status_code=400, detail="Zip file does not contain .sql or .txt files")
    return scripts


def _write_batch_exports(result: dict[str, object]) -> dict[str, str]:
    run_id = f"lineage_batch_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    json_path = RESULTS_DIR / f"{run_id}.json"
    excel_path = RESULTS_DIR / f"{run_id}.xlsx"
    write_lineage_json(json_path, result)
    write_lineage_batch_excel(excel_path, result)
    return {"json_filename": json_path.name, "excel_filename": excel_path.name}


def _attach_warnings(result: dict[str, object], warnings: list[dict[str, str]]) -> dict[str, object]:
    if warnings:
        result["warnings"] = warnings + list(result.get("warnings", []))
    return result
