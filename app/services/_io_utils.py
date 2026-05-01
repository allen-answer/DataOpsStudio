from __future__ import annotations

import zipfile

from fastapi import HTTPException


# Limits chosen to bound zip-bomb risk on user uploads (lineage scripts and schema metadata bundles).
ZIP_MAX_FILES = 500
ZIP_MAX_DECOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MB


def decode_sql_content(content: bytes, filename: str) -> str:
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


def check_zip_safety(archive: zipfile.ZipFile) -> None:
    entries = [e for e in archive.infolist() if not e.is_dir()]
    if len(entries) > ZIP_MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Zip file contains too many files (max {ZIP_MAX_FILES})")
    total = sum(e.file_size for e in entries)
    if total > ZIP_MAX_DECOMPRESSED_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Zip file decompressed size exceeds limit ({ZIP_MAX_DECOMPRESSED_BYTES // 1024 // 1024} MB)",
        )


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
