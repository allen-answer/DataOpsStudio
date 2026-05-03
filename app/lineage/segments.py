from __future__ import annotations

import re
from typing import Any


def parse_lineage_statements(sqlglot: Any, sql: str, dialect: str | None) -> list[Any]:
    try:
        return sqlglot.parse(sql, read=dialect or None) + parse_segments(
            sqlglot, extract_replace_segments(sql), dialect, ignore_errors=True
        )
    except Exception:
        statements: list[Any] = []
        errors: list[Exception] = []
        dynamic_sqls = [s["sql"] for s in extract_dynamic_sql_segments(sql)]
        for segment in extract_analyzable_segments(sql) + dynamic_sqls:
            parsed = parse_segments(sqlglot, [segment], dialect, ignore_errors=True)
            if parsed:
                statements.extend(parsed)
            else:
                try:
                    sqlglot.parse(segment, read=dialect or None)
                except Exception as exc:
                    errors.append(exc)
        if statements:
            return statements
        if errors:
            raise errors[0]
        return sqlglot.parse(sql, read=dialect or None)


def extract_replace_segments(sql: str) -> list[str]:
    return [
        segment.strip()
        for segment in re.split(r";", sql)
        if re.match(r"^\s*replace\s+into\b", segment, flags=re.IGNORECASE)
        and re.search(r"\bselect\b", segment, flags=re.IGNORECASE | re.DOTALL)
    ]


def parse_segments(sqlglot: Any, segments: list[str], dialect: str | None, ignore_errors: bool = False) -> list[Any]:
    statements: list[Any] = []
    for segment in segments:
        compatible = _parse_compatible_segment(sqlglot, segment, dialect)
        if compatible:
            statements.extend(compatible)
            continue
        try:
            statements.extend(sqlglot.parse(segment, read=dialect or None))
        except Exception:
            if not ignore_errors:
                raise
    return statements


def _parse_compatible_segment(sqlglot: Any, segment: str, dialect: str | None) -> list[Any]:
    normalized = segment.strip()
    replacements = [
        (r"^replace\s+into\b", "INSERT INTO", "REPLACE"),
    ]
    for pattern, replacement, dml_type in replacements:
        if not re.match(pattern, normalized, flags=re.IGNORECASE):
            continue
        compatible_sql = re.sub(pattern, replacement, normalized, count=1, flags=re.IGNORECASE)
        try:
            parsed = sqlglot.parse(compatible_sql, read=dialect or None)
        except Exception:
            return []
        for statement in parsed:
            setattr(statement, "_lineage_dml_type", dml_type)
            setattr(statement, "_lineage_original_sql", normalized)
        return parsed
    return []


def extract_analyzable_segments(sql: str) -> list[str]:
    segments: list[str] = []
    for raw_segment in re.split(r";", sql):
        segment = raw_segment.strip()
        if not segment:
            continue
        segment = re.sub(r"^(begin|then|else)\b", "", segment, flags=re.IGNORECASE).strip()
        segment = re.sub(r"\bend\s*$", "", segment, flags=re.IGNORECASE).strip()
        if re.match(r"^(with|select|insert|replace\s+into|create\s+(or\s+replace\s+)?procedure|create\s+(or\s+replace\s+)?function|create\s+(or\s+replace\s+)?(temporary\s+|temp\s+)?table)\b", segment, re.IGNORECASE):
            segments.append(segment)
    return segments


_RE_PROC_HEADER = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:DEFINER\s*=\s*\S+\s+)?(?P<kind>PROCEDURE|FUNCTION|PACKAGE\s+BODY|TRIGGER)\s+(?P<name>[\w$#.\"`\[\]]+)",
    flags=re.IGNORECASE,
)
_RE_BEGIN = re.compile(r"\bBEGIN\b", flags=re.IGNORECASE)
_RE_BLOCK_TOKEN = re.compile(r"\bBEGIN\b|\bEND\b", flags=re.IGNORECASE)

# 块级 END 必须是 `END;`、`END proc_name;`、`END$` 或行末。`END IF;` / `END LOOP;`
# / `END CASE;` 和裸 case 表达式里的 `end` 是 PL/SQL 控制流，不算 block 结束。
# 这一组关键字出现在 END 后面、`;` 之前的标识符位置时，整段视为控制流 END，
# 不参与 block-level 计数。
_CONTROL_END_KEYWORDS = frozenset({
    "IF", "LOOP", "CASE", "RECORD", "FOR", "WHILE", "XMLELEMENT",
})


def _is_block_end(body: str, pos: int) -> bool:
    """从 `END` 关键字后面的位置判断是不是 block-level 结束。

    block-level：`END;`、`END identifier;`（identifier 不属于控制流关键字）。
    控制流：`END IF;` / `END LOOP;` / `END CASE;` / 裸 `case when ... end` 等。
    """
    while pos < len(body) and body[pos].isspace():
        pos += 1
    if pos >= len(body):
        return True  # 文件末尾的裸 END 也当 block 结束（一般没分号）
    if body[pos] == ";":
        return True
    if not (body[pos].isalpha() or body[pos] == "_"):
        return False
    start = pos
    while pos < len(body) and (body[pos].isalnum() or body[pos] == "_"):
        pos += 1
    ident = body[start:pos].upper()
    if ident in _CONTROL_END_KEYWORDS:
        return False
    while pos < len(body) and body[pos].isspace():
        pos += 1
    return pos < len(body) and body[pos] == ";"
_RE_BODY_DML = re.compile(
    r"\b(WITH|SELECT|INSERT|REPLACE\s+INTO|UPDATE|DELETE|MERGE|CREATE\s+(?:OR\s+REPLACE\s+)?(?:GLOBAL\s+TEMPORARY\s+|TEMPORARY\s+|TEMP\s+)?TABLE|TRUNCATE)\b",
    flags=re.IGNORECASE,
)
# 纯空白 + 行注释（`--`） + 块注释（`/* */`）的组合。fullmatch 这个表示 DML 前面
# 没有控制流壳子（IF/THEN、CASE 等），可以原样保留——业务标题就在前缀注释里。
_RE_PURE_COMMENT_PREFIX = re.compile(r"(?:\s|--[^\n]*|/\*(?:[^*]|\*(?!/))*\*/)*")


def extract_procedure_segments(sql: str) -> list[dict[str, str]]:
    """Extract DML statements nested inside CREATE PROCEDURE/FUNCTION/PACKAGE BODY/TRIGGER blocks.

    Skips control-flow shells (IF/LOOP/EXCEPTION) and nested BEGIN/END blocks via token-balanced scan.

    Important: 段落保留原始换行——`-- 行注释` 必须靠换行终止才不会把后面的列名一起吞掉。
    用规范化文本（空白压平）做 dedupe key，原始格式喂给 sqlglot 解析。
    """
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for header in _RE_PROC_HEADER.finditer(sql):
        name = header.group("name").strip()
        kind = " ".join(header.group("kind").split()).upper()
        body_start = _RE_BEGIN.search(sql, pos=header.end())
        if not body_start:
            continue
        depth = 1
        cursor = body_start.end()
        body_end = len(sql)
        for tok_match in _RE_BLOCK_TOKEN.finditer(sql, pos=cursor):
            tok = tok_match.group(0).upper()
            if tok == "BEGIN":
                depth += 1
            else:
                # END 必须是 block-level（`END;` / `END name;`）才算；`END IF;`、
                # 裸 `case ... end`、`END LOOP;` 是控制流 END，跳过。这是过去
                # `t_etl_zqsz` 区域 INSERT 被误吞的根因。
                if not _is_block_end(sql, tok_match.end()):
                    continue
                depth -= 1
                if depth == 0:
                    body_end = tok_match.start()
                    break
        body = sql[body_start.end():body_end]
        for index, segment in enumerate(_iter_procedure_body_segments(body), start=1):
            preserved = clean_procedure_segment(segment)
            if not preserved:
                continue
            dedupe_key = " ".join(preserved.split())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            result.append(
                {
                    "procedure_name": name,
                    "procedure_kind": kind,
                    "segment_index": str(index),
                    "sql": preserved,
                    "confidence": "high",
                }
            )
    return result


def clean_procedure_segment(segment: str) -> str:
    """Strip leading/trailing whitespace and the trailing `;`, but **keep** newlines.

    `-- line comments` 必须靠换行终止；如果把所有空白压成单空格，注释会一直吞到行末，
    导致 sqlglot 把列名 / 表达式都识别成注释、括号失配。所以过程体段不能用
    `clean_dynamic_segment`（它是给单行字符串字面量用的）。
    """
    return segment.strip().rstrip(";").strip()


def _iter_procedure_body_segments(body: str) -> list[str]:
    """Split a procedure body into top-level statements, skipping control-flow shells."""
    segments: list[str] = []
    depth = 0
    buffer: list[str] = []
    pos = 0
    length = len(body)
    while pos < length:
        char = body[pos]
        # Skip string literals to avoid splitting on ; inside them.
        if char in "'\"":
            quote = char
            buffer.append(char)
            pos += 1
            while pos < length:
                buffer.append(body[pos])
                if body[pos] == quote and (pos + 1 >= length or body[pos + 1] != quote):
                    pos += 1
                    break
                if body[pos] == quote:
                    buffer.append(body[pos + 1])
                    pos += 2
                    continue
                pos += 1
            continue
        # Track nested BEGIN/END so a ; inside a nested block doesn't end the outer segment.
        if char.isalpha():
            tail = body[pos:pos + 6].upper()
            if tail.startswith("BEGIN") and (pos + 5 >= length or not body[pos + 5].isalnum()):
                depth += 1
                buffer.append(body[pos:pos + 5])
                pos += 5
                continue
            if tail.startswith("END") and (pos + 3 >= length or not body[pos + 3].isalnum()):
                # 同 _is_block_end 逻辑：仅 block-level END 参与 depth 计数。
                # 不然 `case when ... end` / `end if;` / `end loop;` 会让 depth 错位
                # 进而漏切后续 INSERT。
                if _is_block_end(body, pos + 3) and depth > 0:
                    depth -= 1
                buffer.append(body[pos:pos + 3])
                pos += 3
                continue
        if char == ";" and depth == 0:
            segments.append("".join(buffer).strip())
            buffer = []
            pos += 1
            continue
        buffer.append(char)
        pos += 1
    tail = "".join(buffer).strip()
    if tail:
        segments.append(tail)
    cleaned: list[str] = []
    for seg in segments:
        # The segment may start with control-flow shells (IF ... THEN, ELSIF ... THEN, FOR ... LOOP).
        # Find the first DML keyword and analyze from there.
        match = _RE_BODY_DML.search(seg)
        if not match:
            continue
        prefix = seg[:match.start()]
        # 如果 DML 前面只有空白和 line/block 注释（业务标题 `-- 集中交易`），保留 ——
        # sqlglot 会把它挂到 statement.comments 上做 statement_title。
        # 反之（IF/THEN、CASE 之类的控制流壳子），仍然剥掉。
        if _RE_PURE_COMMENT_PREFIX.fullmatch(prefix):
            cleaned.append(seg.strip())
        else:
            cleaned.append(seg[match.start():].strip())
    return cleaned


def extract_dynamic_sql_segments(sql: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []

    def add(segment: str, source: str, confidence: str) -> None:
        cleaned = clean_dynamic_segment(segment)
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        result.append({"sql": cleaned, "source": source, "confidence": confidence})

    keyword_pattern = re.compile(
        r"(?:execute\s+immediate|exec(?:ute)?\s+(?:sys\.)?sp_executesql)\s+(?:N)?'((?:''|[^'])*)'",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in keyword_pattern.finditer(sql):
        add(_unescape_sql_string(match.group(1)), "execute_keyword", "high")

    # MySQL: SET @sql := '...'; PREPARE stmt FROM @sql; EXECUTE stmt;
    set_vars = _capture_session_var_assignments(sql)
    prepare_pattern = re.compile(
        r"\bPREPARE\s+\w+\s+FROM\s+(?:@(\w+)|(?:N)?'((?:''|[^'])*)')",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in prepare_pattern.finditer(sql):
        var_name, literal = match.group(1), match.group(2)
        if literal:
            add(_unescape_sql_string(literal), "prepare_literal", "high")
        elif var_name and var_name.lower() in set_vars:
            for assignment in set_vars[var_name.lower()]:
                add(assignment["sql"], "prepare_var", assignment["confidence"])

    # Oracle/PL-SQL: v_sql := 'INSERT INTO ' || p_t || ' SELECT ...'; EXECUTE IMMEDIATE v_sql;
    plsql_vars = _capture_plsql_var_assignments(sql)
    immediate_var_pattern = re.compile(
        r"\bEXECUTE\s+IMMEDIATE\s+([\w$#]+)\s*[;\n]",
        flags=re.IGNORECASE,
    )
    for match in immediate_var_pattern.finditer(sql):
        var_name = match.group(1).lower()
        if var_name in plsql_vars:
            for assignment in plsql_vars[var_name]:
                add(assignment["sql"], "var_concat", assignment["confidence"])

    # 之前还有一条 `string_pattern` 兜底——任何 20+ 字符的字符串字面量都被
    # 当成动态 SQL 候选。在大型存储过程里这会产生海量误报（中文注释片段、
    # 错误信息字面量、报表 header 等都被命中），且已通过的 EXECUTE / PREPARE
    # / 变量赋值三条精确路径覆盖了真正的动态 SQL 场景。所以这里不再做字面量
    # 兜底——宁可漏识别一条，也不报 324 段假动态 SQL。如果未来要救回这条
    # 兜底，必须强约束字面量本身以 SELECT/INSERT/UPDATE/MERGE/DELETE/WITH 开头。

    return result


_RE_SET_ASSIGNMENT = re.compile(r"^\s*SET\s+@(\w+)\s*:?=\s*(.+)$", flags=re.IGNORECASE | re.DOTALL)
_RE_PLSQL_ASSIGNMENT = re.compile(r"^\s*([\w$#]+)\s*:=\s*(.+)$", flags=re.IGNORECASE | re.DOTALL)


def _capture_session_var_assignments(sql: str) -> dict[str, list[dict[str, str]]]:
    return _capture_var_assignments(sql, _RE_SET_ASSIGNMENT)


def _capture_plsql_var_assignments(sql: str) -> dict[str, list[dict[str, str]]]:
    return _capture_var_assignments(sql, _RE_PLSQL_ASSIGNMENT)


def _capture_var_assignments(
    sql: str, assignment_re: re.Pattern[str]
) -> dict[str, list[dict[str, str]]]:
    # Strip block-shell tokens so var assignments inside procedure bodies become top-level statements.
    flat = re.sub(r"\b(BEGIN|END|DECLARE|THEN|ELSE|ELSIF|LOOP)\b", " ", sql, flags=re.IGNORECASE)
    result: dict[str, list[dict[str, str]]] = {}
    for segment in _split_top_level_statements(flat):
        match = assignment_re.match(segment)
        if not match:
            continue
        name = match.group(1).lower()
        rhs = match.group(2).strip()
        if "||" in rhs or rhs.upper().startswith("CONCAT"):
            rebuilt = _rebuild_concat(rhs)
            if rebuilt and _looks_like_lineage_sql(rebuilt):
                result.setdefault(name, []).append({"sql": rebuilt, "confidence": "low"})
            continue
        if rhs.startswith("'") or rhs[:2].upper() == "N'":
            literal = _strip_quoted(rhs)
            if _looks_like_lineage_sql(literal):
                result.setdefault(name, []).append({"sql": literal, "confidence": "high"})
    return result


def _split_top_level_statements(sql: str) -> list[str]:
    """Split a SQL script into top-level statements, respecting quoted strings."""
    statements: list[str] = []
    buf: list[str] = []
    pos = 0
    length = len(sql)
    while pos < length:
        char = sql[pos]
        if char in "'\"":
            quote = char
            buf.append(char)
            pos += 1
            while pos < length:
                buf.append(sql[pos])
                if sql[pos] == quote and (pos + 1 >= length or sql[pos + 1] != quote):
                    pos += 1
                    break
                if sql[pos] == quote:
                    buf.append(sql[pos + 1])
                    pos += 2
                    continue
                pos += 1
            continue
        if char == ";":
            statements.append("".join(buf).strip())
            buf = []
            pos += 1
            continue
        buf.append(char)
        pos += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s]


def _strip_quoted(value: str) -> str:
    cleaned = value.strip()
    if cleaned[:1].upper() == "N":
        cleaned = cleaned[1:]
    if cleaned.startswith("'") and cleaned.endswith("'"):
        cleaned = cleaned[1:-1]
    return _unescape_sql_string(cleaned)


def _rebuild_concat(expression: str) -> str:
    """Reassemble literal-only segments of a concat; replace variable parts with :placeholders.

    Handles two forms: 'lit' || var || 'lit' (PL/SQL) and CONCAT('lit', var, 'lit') (MySQL).
    Variable segments become :var so sqlglot can still parse the resulting SQL.
    """
    expression = expression.strip()
    parts: list[str]
    concat_match = re.match(r"CONCAT\s*\((.*)\)\s*$", expression, flags=re.IGNORECASE | re.DOTALL)
    if concat_match:
        parts = _split_top_level(concat_match.group(1), ",")
    else:
        parts = _split_top_level(expression, "||")
    rebuilt: list[str] = []
    for raw in parts:
        item = raw.strip()
        if not item:
            continue
        if item.startswith("'") or item[:2].upper() == "N'":
            rebuilt.append(_strip_quoted(item))
        else:
            placeholder = re.sub(r"\W+", "_", item).strip("_") or "var"
            rebuilt.append(f":{placeholder}")
    return "".join(rebuilt)


def _split_top_level(expression: str, delimiter: str) -> list[str]:
    """Split on delimiter respecting parentheses and quoted strings."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    pos = 0
    delim_len = len(delimiter)
    while pos < len(expression):
        char = expression[pos]
        if char == "'":
            buf.append(char)
            pos += 1
            while pos < len(expression):
                buf.append(expression[pos])
                if expression[pos] == "'" and (pos + 1 >= len(expression) or expression[pos + 1] != "'"):
                    pos += 1
                    break
                if expression[pos] == "'":
                    buf.append(expression[pos + 1])
                    pos += 2
                    continue
                pos += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth == 0 and expression[pos:pos + delim_len] == delimiter:
            parts.append("".join(buf))
            buf = []
            pos += delim_len
            continue
        buf.append(char)
        pos += 1
    parts.append("".join(buf))
    return parts


def _unescape_sql_string(value: str) -> str:
    return value.replace("''", "'")


def clean_dynamic_segment(segment: str) -> str:
    return " ".join(segment.strip().rstrip(";").split())


def _looks_like_lineage_sql(segment: str) -> bool:
    cleaned = segment.strip()
    if not cleaned:
        return False
    return bool(
        re.match(r"^(with|select|insert)\b", cleaned, flags=re.IGNORECASE)
        or re.search(r"\b(insert|replace)\s+into\b.+\bselect\b", cleaned, flags=re.IGNORECASE | re.DOTALL)
        or re.search(r"\bcreate\s+(or\s+replace\s+)?(temporary\s+|temp\s+)?table\b.+\bas\s+select\b", cleaned, flags=re.IGNORECASE | re.DOTALL)
    )
