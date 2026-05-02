from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import CompareTask, DataSource
from app.utils.paths import DATASOURCES_FILE, RESULTS_DIR, TASKS_FILE


def export_config(include_passwords: bool = False) -> Path:
    """导出 datasources + tasks 到 results/config_export_{ts}.json。

    `include_passwords` 默认 False —— 导出文件给别人 / 上传到协作平台时
    不应该带明文密码。需要带密码（如自己备份回灌）时显式传 True。
    """
    datasources = _read_json_array(DATASOURCES_FILE)
    if not include_passwords:
        for ds in datasources:
            if "password" in ds:
                ds["password"] = ""
    payload = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "passwords_included": include_passwords,
        "datasources": datasources,
        "tasks": _read_json_array(TASKS_FILE),
    }
    path = RESULTS_DIR / f"config_export_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def import_config(content: bytes) -> dict[str, int]:
    payload = json.loads(content.decode("utf-8-sig"))
    datasources = payload.get("datasources", [])
    tasks = payload.get("tasks", [])
    if not isinstance(datasources, list) or not isinstance(tasks, list):
        raise ValueError("config import file must contain datasources and tasks arrays")
    validated_datasources = [DataSource.model_validate(item).model_dump(mode="json") for item in datasources]
    validated_tasks = [CompareTask.model_validate(item).model_dump(mode="json") for item in tasks]
    DATASOURCES_FILE.write_text(json.dumps(validated_datasources, ensure_ascii=False, indent=2), encoding="utf-8")
    TASKS_FILE.write_text(json.dumps(validated_tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    from app.services.repositories import datasource_store, task_store

    datasource_store.invalidate_cache()
    task_store.invalidate_cache()
    return {"datasources": len(validated_datasources), "tasks": len(validated_tasks)}


def _read_json_array(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8").strip() or "[]")
    if not isinstance(data, list):
        raise ValueError(f"{path.name} must contain a JSON array")
    return data
