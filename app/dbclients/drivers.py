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
    elif db_type == DatabaseType.DM:
        add_dm_dll_directories()
    for module in DRIVER_MODULES[db_type]:
        if importlib.util.find_spec(module):
            return module
    return None


def _site_packages_dirs() -> list[Path]:
    """返回 sys.path 里所有 site-packages 路径。

    Portable / venv / system Python 三种部署下,dmPython 包 + 它的 .libs 目录
    都在某个 site-packages 下,具体路径不同,所以扫 sys.path。
    """
    import sys
    out: list[Path] = []
    for p in sys.path:
        if not p:
            continue
        pp = Path(p)
        if pp.name == "site-packages" and pp.is_dir():
            out.append(pp)
    return out


def add_dm_dll_directories() -> list[str]:
    """把 dmPython 依赖的 native DLL 目录加进 Windows DLL search path。

    **为什么需要**:dmPython.pyd 加载时需要 dmsvc.dll / dmcalc.dll / dmstr.dll 等
    30+ 个 native DLL,通常打包在 site-packages/dmpython.libs/ 或
    site-packages/dmPython.libs/。
    Windows LoadLibrary 默认只搜:exe 目录 / System32 / PATH 列出的目录。
    site-packages 子目录默认**不在**搜索路径里,导致 import dmPython 时报
    "DLL load failed: 找不到指定的模块"。

    扫 site-packages 找 dmpython.libs(numpy 风格)+ dmPython 包内部的 lib/dll 目录,
    add_dll_directory + 加进 PATH。**幂等**(已加过的路径不重复 set,防 PATH 溢出)。
    """
    added: list[str] = []
    candidates: list[Path] = []
    for sp in _site_packages_dirs():
        # numpy 风格:<pkg>.libs/ 兄弟目录
        for libs_dir in (sp / "dmpython.libs", sp / "dmPython.libs"):
            if libs_dir.is_dir():
                candidates.append(libs_dir)
        # dmPython 包内可能也带 DLL
        pkg_dir = sp / "dmPython"
        if pkg_dir.is_dir():
            candidates.append(pkg_dir)
            for sub in ("lib", "bin", "dll"):
                d = pkg_dir / sub
                if d.is_dir():
                    candidates.append(d)
    for env_name in ("DM_HOME", "DM_DLL_DIR"):
        env_value = os.environ.get(env_name)
        if env_value:
            candidates.append(Path(env_value))

    for directory in candidates:
        if not directory.exists():
            continue
        path = str(directory)
        current_path = os.environ.get("PATH", "")
        if path.lower() not in current_path.lower().split(os.pathsep):
            os.environ["PATH"] = path + os.pathsep + current_path
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(path)
            except (OSError, FileNotFoundError):
                pass
        added.append(path)
    return added


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
