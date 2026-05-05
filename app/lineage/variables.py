from __future__ import annotations

import re

from app.lineage._common import unique_strings


def script_variables(sql: str) -> list[dict[str, str]]:
    variables: list[dict[str, str]] = []
    seen: set[str] = set()
    for variable in variable_names(sql):
        seen.add(variable.lower())
        variables.append(
            {
                "name": variable,
                "placeholder": variable,
                "assigned_value": assigned_value(sql, variable),
            }
        )
    # S5 PR3：把 PL/SQL PACKAGE BODY / DECLARE 节里的 CONSTANT / 普通变量声明
    # 也作为 script_variables 暴露出来。这样前端变量面板能展示，且后续 PR
    # 可以把 insert_mapping.expression 里出现的 `g_xxx` 关联到声明值。
    for entry in package_variables(sql):
        if entry["name"].lower() in seen:
            continue
        seen.add(entry["name"].lower())
        variables.append(entry)
    return variables


def variable_names(sql: str) -> list[str]:
    names: list[str] = []
    patterns = [
        r"\$\{\s*([A-Za-z_][\w$#]*)\s*\}",
        r"(?<!:):([A-Za-z_][\w$#]*)",
        r"@([A-Za-z_][\w$#]*)",
    ]
    for pattern in patterns:
        names.extend(match.group(1) for match in re.finditer(pattern, sql))
    return unique_strings(names)


def assigned_value(sql: str, variable: str) -> str:
    escaped = re.escape(variable)
    patterns = [
        rf"\b{escaped}\b\s*:=\s*(.*?)(?:;|\n|$)",
        rf"\b{escaped}\b\s*=\s*(.*?)(?:;|\n|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, sql, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return " ".join(match.group(1).strip().split())
    return ""


# S5 PR3：PL/SQL PACKAGE BODY / DECLARE 节的变量 / 常量声明 ──────────────
#
# Oracle PL/SQL 里两种典型形态：
#   PACKAGE BODY pkg_x AS
#     g_const CONSTANT VARCHAR2(32) := 'JY';
#     g_var   NUMBER := 0;
#     PROCEDURE p IS BEGIN ... END;
#   END;
#
#   DECLARE
#     v_cnt NUMBER := 100;
#   BEGIN ... END;
#
# 这里只抽 PACKAGE BODY 顶层（IS/AS 之后到第一个 PROCEDURE/FUNCTION 之前）和
# DECLARE 块里的声明 —— 过程体内的局部变量靠下游的 `assigned_value()` 兜底。
# 形如 `name [CONSTANT] TYPE [(N[,M])] [:= literal_or_expr];`
_RE_PACKAGE_BODY_HEAD = re.compile(
    r"\bPACKAGE\s+BODY\s+[\w$#.]+\s+(?:IS|AS)\b",
    flags=re.IGNORECASE,
)
_RE_DECLARE_HEAD = re.compile(r"\bDECLARE\b", flags=re.IGNORECASE)
_RE_FIRST_PROC_OR_FUNC = re.compile(
    r"\b(?:PROCEDURE|FUNCTION|BEGIN)\b",
    flags=re.IGNORECASE,
)
# 一行声明：`name [CONSTANT] TYPE := value;`（type 可能含 (n) 或 (n,m)，可能引用 %TYPE）
_RE_DECLARATION = re.compile(
    r"^\s*([A-Za-z_][\w$#]*)\s+(CONSTANT\s+)?"
    r"(?:[A-Za-z_][\w$#.]*(?:\s*\(\s*\d+\s*(?:,\s*\d+\s*)?\))?(?:%TYPE)?)\s*"
    r"(?::=\s*(.+?))?\s*;",
    flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def package_variables(sql: str) -> list[dict[str, str]]:
    """抽 PACKAGE BODY 顶层 / DECLARE 节的变量与常量声明。

    返回列表元素与 script_variables 兼容（name / placeholder / assigned_value），
    额外带 `kind`（package_constant / package_variable / declare_constant /
    declare_variable）方便前端区分展示。
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for region_text, kind_prefix in _iter_declaration_regions(sql):
        for m in _RE_DECLARATION.finditer(region_text):
            name = m.group(1)
            is_const = bool(m.group(2))
            value = (m.group(3) or "").strip()
            # 排除 PL/SQL 关键字误识别（CURSOR / TYPE / PROCEDURE / 等）—— 它们
            # 跟 declaration 形态相似但不是变量。先用一个保守黑名单。
            if name.upper() in _DECL_NOISE:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "name": name,
                "placeholder": name,
                "assigned_value": " ".join(value.split()) if value else "",
                "kind": f"{kind_prefix}_{'constant' if is_const else 'variable'}",
            })
    return out


_DECL_NOISE = {
    "CURSOR", "TYPE", "SUBTYPE", "PROCEDURE", "FUNCTION", "BEGIN", "END",
    "IF", "ELSIF", "ELSE", "FOR", "WHILE", "LOOP", "CASE", "WHEN", "THEN",
    "RETURN", "EXCEPTION", "PRAGMA",
}


def _iter_declaration_regions(sql: str) -> list[tuple[str, str]]:
    """切出可能含变量声明的代码区。返回 [(region_text, kind_prefix)]。

    kind_prefix:
      - "package" —— PACKAGE BODY 头到第一个 PROCEDURE/FUNCTION/BEGIN
      - "declare" —— DECLARE 到 BEGIN
    """
    regions: list[tuple[str, str]] = []
    # PACKAGE BODY 顶层
    for m in _RE_PACKAGE_BODY_HEAD.finditer(sql):
        start = m.end()
        end_match = _RE_FIRST_PROC_OR_FUNC.search(sql, start)
        end = end_match.start() if end_match else len(sql)
        if end > start:
            regions.append((sql[start:end], "package"))
    # DECLARE 块
    for m in _RE_DECLARE_HEAD.finditer(sql):
        start = m.end()
        # DECLARE 后的首个 BEGIN 是边界（不能用 PROCEDURE/FUNCTION，DECLARE 块
        # 通常嵌在匿名块里）
        end_match = re.search(r"\bBEGIN\b", sql[start:], flags=re.IGNORECASE)
        end = start + end_match.start() if end_match else len(sql)
        if end > start:
            regions.append((sql[start:end], "declare"))
    return regions
