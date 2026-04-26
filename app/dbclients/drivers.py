from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from app.models import DatabaseType


DRIVER_MODULES: dict[DatabaseType, list[str]] = {
    DatabaseType.DM: ["dmPython"],
    DatabaseType.MYSQL: ["pymysql", "MySQLdb"],
    DatabaseType.ORACLE: ["oracledb", "cx_Oracle"],
    DatabaseType.DB2: ["ibm_db_dbi", "ibm_db"],
}


def detect_drivers() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for db_type, modules in DRIVER_MODULES.items():
        installed = [module for module in modules if importlib.util.find_spec(module)]
        result[db_type.value] = {
            "available": bool(installed),
            "installed_modules": installed,
            "candidate_modules": modules,
        }
    return result


def first_available_module(db_type: DatabaseType) -> str | None:
    if db_type == DatabaseType.DB2:
        add_db2_dll_directories()
    for module in DRIVER_MODULES[db_type]:
        if importlib.util.find_spec(module):
            return module
    return None


def add_db2_dll_directories() -> list[str]:
    candidates = []
    for env_name in ("IBM_DB_HOME", "DB2CLI_HOME"):
        env_value = os.environ.get(env_name)
        if env_value:
            candidates.append(Path(env_value))

    candidates.extend(
        [
            Path.home() / "AppData/Local/Programs/Python/clidriver",
            Path.home() / "AppData/Local/Programs/Python/Python312/Lib/site-packages/clidriver",
        ]
    )

    added: list[str] = []
    for root in candidates:
        for directory in (
            root / "bin",
            root / "bin/amd64.VC12.CRT",
            root / "bin/amd64.VC14.CRT",
        ):
            if directory.exists():
                path = str(directory)
                os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(path)
                added.append(path)
    return added
