"""跨模块共用的基础枚举。"""
from __future__ import annotations

from enum import Enum


class DatabaseType(str, Enum):
    DM = "DM"
    MYSQL = "MySQL"
    ORACLE = "Oracle"
    DB2 = "DB2"


class SqlMode(str, Enum):
    SINGLE = "single"
    DOUBLE = "double"


class SourceKind(str, Enum):
    SQL = "sql"
    EXCEL = "excel"
