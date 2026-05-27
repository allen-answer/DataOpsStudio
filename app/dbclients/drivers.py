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
                # **幂等性**:此函数会被 DB2 dialect.connect() 每次连接调一次,
                # 不去重会把 PATH 不断重复前缀膨胀。Windows PATH 上限 32767 字符,
                # 跑几十个 DB2 查询后 os.environ["PATH"] = ... 直接 ValueError,
                # 导致 DB2 数据源不可用 + middleware 海啸 unhandled exception。
                # 检查 path 是否已在 PATH 里(大小写不敏感 — Windows 路径不区分),
                # 已有就跳过 set,只补一次 add_dll_directory。
                current_path = os.environ.get("PATH", "")
                if path.lower() not in current_path.lower().split(os.pathsep):
                    os.environ["PATH"] = path + os.pathsep + current_path
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(path)
                    except (OSError, FileNotFoundError):
                        # add_dll_directory 对同一路径多次调用会抛 (Win API 行为),吞掉
                        pass
                added.append(path)
    return added
