from __future__ import annotations

import re

from app.lineage._common import unique_strings


def script_variables(sql: str) -> list[dict[str, str]]:
    variables: list[dict[str, str]] = []
    for variable in variable_names(sql):
        variables.append(
            {
                "name": variable,
                "placeholder": variable,
                "assigned_value": assigned_value(sql, variable),
            }
        )
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
