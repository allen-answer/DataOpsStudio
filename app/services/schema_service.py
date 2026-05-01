from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.services._io_utils import check_zip_safety, decode_sql_content
from app.services.repositories import datasource_store
from app.services.schema_introspection import fetch_schema_metadata
from app.services.schema_metadata import merge_schema_metadata, parse_schema_metadata


def resolve_lineage_schema(
    sql: str,
    file_schema: dict[str, list[str]] | None,
    datasource_id: str,
    schema_name: str,
    table_filter: str,
    only_sql_tables: bool,
    schema_dialect: str,
) -> tuple[dict[str, list[str]] | None, list[dict[str, str]]]:
    datasource_schema, warnings = _schema_from_datasource(
        datasource_id,
        schema_name,
        table_filter,
        extract_table_candidates(sql) if only_sql_tables else [],
        schema_dialect,
    )
    return _merge(file_schema, datasource_schema), warnings


def schema_from_files(schema_files: list[UploadFile]) -> dict[str, list[str]] | None:
    items: list[dict[str, list[str]]] = []
    for upload in schema_files:
        if not upload or not upload.filename:
            continue
        filename = Path(upload.filename).name
        suffix = Path(filename).suffix.lower()
        content = upload.file.read()
        if suffix == ".zip":
            items.extend(_schema_from_zip(content))
            continue
        if suffix not in {".json", ".sql", ".txt"}:
            raise HTTPException(
                status_code=400,
                detail=f"Only .json, .sql, .txt and .zip schema metadata files are supported: {filename}",
            )
        try:
            items.append(parse_schema_metadata(decode_sql_content(content, filename)))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid schema metadata {filename}: {exc}") from exc
    merged = merge_schema_metadata(*items)
    return merged or None


def extract_table_candidates(sql: str) -> list[str]:
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


def _schema_from_datasource(
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


def _merge(*items: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
    merged = merge_schema_metadata(*items)
    return merged or None


def _schema_from_zip(content: bytes) -> list[dict[str, list[str]]]:
    items: list[dict[str, list[str]]] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid schema metadata zip file") from exc
    check_zip_safety(archive)
    for item in archive.infolist():
        if item.is_dir():
            continue
        suffix = Path(item.filename).suffix.lower()
        if suffix not in {".json", ".sql", ".txt"}:
            continue
        name = item.filename.replace("\\", "/")
        try:
            items.append(parse_schema_metadata(decode_sql_content(archive.read(item), name)))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid schema metadata {name}: {exc}") from exc
    if not items:
        raise HTTPException(
            status_code=400,
            detail="Schema metadata zip file does not contain .json, .sql or .txt files",
        )
    return items
