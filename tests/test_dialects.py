"""Unit tests for app.lineage.dialects.resolve_dialect.

Covers the dialect-name → sqlglot-dialect mapping that gates how
procedure bodies, dynamic SQL fragments, and identifier quoting
are parsed. Aliases live close to user-facing inputs (UI dropdown,
form fields, JSON payloads), so a regression here would silently
mis-parse an entire script.
"""
import pytest

from app.lineage.dialects import resolve_dialect


@pytest.mark.parametrize(
    "alias,expected",
    [
        # Pass-through dialects
        ("mysql", "mysql"),
        ("oracle", "oracle"),
        # DM aliases → oracle (DM 的语法绝大部分继承自 Oracle)
        ("dm", "oracle"),
        ("dameng", "oracle"),
        # OceanBase 区分 mysql / oracle 两种兼容模式
        ("ob_mysql", "mysql"),
        ("oceanbase", "mysql"),
        ("oceanbase_mysql", "mysql"),
        ("obmysql", "mysql"),
        ("ob_oracle", "oracle"),
        ("oceanbase_oracle", "oracle"),
        ("oboracle", "oracle"),
    ],
)
def test_known_aliases_route_correctly(alias, expected):
    assert resolve_dialect(alias) == expected


def test_none_returns_none():
    assert resolve_dialect(None) is None


def test_empty_string_returns_none():
    assert resolve_dialect("") is None


def test_whitespace_only_returns_none():
    assert resolve_dialect("   ") is None


@pytest.mark.parametrize("alias", ["MySQL", "ORACLE", "OB_Mysql", "DamEng"])
def test_aliases_are_case_insensitive(alias):
    assert resolve_dialect(alias) is not None
    # 与全小写版本结果一致
    assert resolve_dialect(alias) == resolve_dialect(alias.lower())


def test_leading_trailing_whitespace_stripped():
    assert resolve_dialect("  oracle  ") == "oracle"
    assert resolve_dialect("\tdm\n") == "oracle"


def test_unknown_dialect_passes_through_unchanged():
    # sqlglot 自身能识别 postgres / tsql / snowflake 等，未列入别名表的应原样下传
    assert resolve_dialect("postgres") == "postgres"
    assert resolve_dialect("tsql") == "tsql"
    assert resolve_dialect("snowflake") == "snowflake"


def test_unknown_dialect_normalized_to_lowercase():
    # 即便我们不识别，也保留 lower() 以让 sqlglot 端获得稳定输入
    assert resolve_dialect("Postgres") == "postgres"
