from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class _BodySegment:
    """过程体内一段 DML，带原文位置和前置注释。

    text          —— 段落原始文本（含前置注释；注释要保留下来给 sqlglot 挂到 statement.comments）
    dml_start     —— DML 关键字（INSERT/UPDATE/...）在 body 中的起始 offset，
                     当作"语义起始行"——business title 注释不计入。
    end           —— 段落（含 ;）在 body 中的结束 offset。
    preceding_comment —— 提取出来的业务标题注释（已 strip `--` / `/* */` 标记），无则空串。
    cursor_sources —— S5：如果段来自 `FOR rec IN (SELECT ... FROM tables) LOOP <body>`
                     上下文，记录 cursor SELECT 引用的源表。让 analyzer 给 body 里
                     用 `rec.col` 写入下游表的 INSERT 补出 source → target 边
                     （否则 INSERT VALUES (rec.col) 没 source_tables，graph 断链）。
    """
    text: str
    dml_start: int
    end: int
    preceding_comment: str
    cursor_sources: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.cursor_sources is None:
            self.cursor_sources = []


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
        if re.match(r"^(with|select|insert|replace\s+into|update|delete|merge|truncate|create\s+(or\s+replace\s+)?procedure|create\s+(or\s+replace\s+)?function|create\s+(or\s+replace\s+)?(temporary\s+|temp\s+)?table)\b", segment, re.IGNORECASE):
            segments.append(segment)
    return segments


_RE_PROC_HEADER = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:DEFINER\s*=\s*\S+\s+)?(?P<kind>PROCEDURE|FUNCTION|PACKAGE\s+BODY|TRIGGER)\s+(?P<name>[\w$#.\"`\[\]]+)",
    flags=re.IGNORECASE,
)
# S5 PR13：嵌套在 PACKAGE BODY 里的 PROCEDURE / FUNCTION 没有 CREATE 前缀。
# 单独再扫一遍，但只在已知 PACKAGE BODY block 范围内用 —— 否则随处一段
# `PROCEDURE foo IS` 都会误识别。
_RE_NESTED_PROC_HEADER = re.compile(
    r"\b(?P<kind>PROCEDURE|FUNCTION)\s+(?P<name>[\w$#.\"`\[\]]+)\b",
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

# Cursor FOR loop 头部：`FOR rec IN (SELECT ... ) LOOP <body>` —— LOOP 之前的部分
# 整体是控制流壳子，里面的 SELECT 是 cursor 子查询，不应被当成顶层 DML。要从 LOOP
# 关键字之后才开始找真正的 DML。`_strip_cursor_for_prefix` 把段里的 LOOP 之前
# 部分剥掉（保留括号配平）。
_RE_CURSOR_FOR_LOOP_HEAD = re.compile(
    r"^\s*FOR\s+[\w$#]+\s+IN\b",
    flags=re.IGNORECASE,
)
_RE_LOOP_KEYWORD = re.compile(r"\bLOOP\b", flags=re.IGNORECASE)
# S5 PR2：扫整个 body 找 cursor FOR loop 范围用（非锚定开头）。
_RE_CURSOR_FOR_LOOP_INLINE = re.compile(
    r"\bFOR\s+[\w$#]+\s+IN\b",
    flags=re.IGNORECASE,
)
# S5 PR7：显式 CURSOR 声明 `CURSOR <name> [IS|AS] SELECT ...;`。配合
# `FOR rec IN <name> LOOP` 用 —— 把 cursor 名映射到声明体里的源表。
# PR8：可选参数列表 `CURSOR cur(p NUMBER, q VARCHAR2) IS SELECT ...;`
_RE_CURSOR_DECL = re.compile(
    r"\bCURSOR\s+([A-Za-z_][\w$#]*)\s*(?:\([^)]*\))?\s+(?:IS|AS)\s+",
    flags=re.IGNORECASE,
)


def _collect_cursor_decls(body: str) -> dict[str, list[str]]:
    """S5 PR7：扫 body 找所有 `CURSOR <name> IS SELECT ...;` 声明。
    返回 {cursor_name_lower: [source_tables]}。

    `FOR rec IN <cursor_name> LOOP` 这种引用形式靠这个映射回填 cursor_sources。
    SELECT 体的解析用同样的 _extract_cursor_select_tables，跳字符串。
    """
    decls: dict[str, list[str]] = {}
    pos = 0
    length = len(body)
    while pos < length:
        m = _RE_CURSOR_DECL.search(body, pos)
        if not m:
            break
        cursor_name = m.group(1)
        select_start = m.end()
        # 找匹配的 ; 在顶层（depth=0）。SELECT 子查询里可能有 ( ) 嵌套
        depth = 0
        j = select_start
        while j < length:
            ch = body[j]
            if ch in "'\"":
                quote = ch
                j += 1
                while j < length:
                    if body[j] == quote:
                        if j + 1 < length and body[j + 1] == quote:
                            j += 2
                            continue
                        j += 1
                        break
                    j += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == ";" and depth == 0:
                break
            j += 1
        select_text = body[select_start:j]
        tables = _extract_cursor_select_tables(select_text)
        if tables:
            decls[cursor_name.lower()] = tables
        pos = j + 1 if j < length else length
    return decls


def _collect_loop_scopes(body: str, extra_cursor_decls: dict[str, list[str]] | None = None) -> list[tuple[int, int, list[str]]]:
    """S5 PR2：扫 body 找所有 `FOR rec IN (...) LOOP ... END LOOP;` 范围。

    返回 [(scope_start, scope_end, cursor_sources)]，scope_start 是 `FOR` 起点，
    scope_end 是匹配的 `END LOOP` 之后的位置。给 _iter_procedure_body_segments
    的二次 pass 用：cursor LOOP 体内多个 DML 段都该继承同一份 cursor_sources。

    嵌套 LOOP 都独立记录；segment 落在哪个 scope 里就用哪个（取最内层）。
    字符串内的 LOOP/END LOOP 关键字会被字符串 skip 跳过。

    S5 PR7：除了 `FOR rec IN (SELECT ...) LOOP` 内联形式，也支持
    `FOR rec IN <cursor_name> LOOP` 显式 cursor 引用 —— 通过 `cursor_decls`
    映射查到声明的源表。`cursor_decls` 由 `_collect_cursor_decls(body)` + 上层
    传入的 extra_cursor_decls（过程 IS/AS 段的 cursor 声明）合并。
    """
    scopes: list[tuple[int, int, list[str]]] = []
    cursor_decls = _collect_cursor_decls(body)
    if extra_cursor_decls:
        for name, tables in extra_cursor_decls.items():
            cursor_decls.setdefault(name, list(tables))
    pos = 0
    length = len(body)
    while pos < length:
        m = _RE_CURSOR_FOR_LOOP_INLINE.search(body, pos)
        if not m:
            break
        for_start = m.start()
        cursor_in_end = m.end()
        # 跳空白找 cursor SELECT 的 ( 或 cursor 名字
        j = cursor_in_end
        while j < length and body[j].isspace():
            j += 1
        cursor_sources: list[str] = []
        if j < length and body[j] == "(":
            # 内联 SELECT 形式：FOR rec IN (SELECT ...) LOOP
            depth = 1
            select_start = j + 1
            j += 1
            while j < length and depth > 0:
                ch = body[j]
                if ch in "'\"":
                    quote = ch
                    j += 1
                    while j < length:
                        if body[j] == quote:
                            if j + 1 < length and body[j + 1] == quote:
                                j += 2
                                continue
                            j += 1
                            break
                        j += 1
                    continue
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        select_end = j
                        j += 1
                        break
                j += 1
            if depth != 0:
                pos = cursor_in_end
                continue
            cursor_sources = _extract_cursor_select_tables(body[select_start:select_end])
        elif j < length and (body[j].isalpha() or body[j] == "_"):
            # PR7：显式 cursor 名字引用形式 FOR rec IN cur_name LOOP
            # PR8：可能有调用参数 FOR rec IN cur_name(arg1, arg2) LOOP
            name_start = j
            while j < length and (body[j].isalnum() or body[j] in "_$#."):
                j += 1
            cursor_name = body[name_start:j].lower()
            cursor_sources = list(cursor_decls.get(cursor_name, []))
            # PR8：跳过可选的调用参数列表 (...)
            while j < length and body[j].isspace():
                j += 1
            if j < length and body[j] == "(":
                arg_depth = 1
                j += 1
                while j < length and arg_depth > 0:
                    ch = body[j]
                    if ch in "'\"":
                        quote = ch
                        j += 1
                        while j < length:
                            if body[j] == quote:
                                if j + 1 < length and body[j + 1] == quote:
                                    j += 2
                                    continue
                                j += 1
                                break
                            j += 1
                        continue
                    if ch == "(":
                        arg_depth += 1
                    elif ch == ")":
                        arg_depth -= 1
                    j += 1
            # 后面可能有 reverse / 数字范围（FOR i IN 1..N LOOP）—— 不是 cursor，
            # 没声明就当无 source 处理，不阻断 LOOP 范围识别
        else:
            pos = cursor_in_end
            continue
        # 跳空白找 LOOP 关键字
        while j < length and body[j].isspace():
            j += 1
        if body[j:j + 4].upper() != "LOOP" or (j + 4 < length and body[j + 4].isalnum()):
            pos = cursor_in_end
            continue
        # 从 LOOP 之后找匹配的 END LOOP（按 LOOP / END LOOP 配对，跳字符串）
        k = j + 4
        loop_depth = 1
        while k < length and loop_depth > 0:
            ch = body[k]
            if ch in "'\"":
                quote = ch
                k += 1
                while k < length:
                    if body[k] == quote:
                        if k + 1 < length and body[k + 1] == quote:
                            k += 2
                            continue
                        k += 1
                        break
                    k += 1
                continue
            if ch.isalpha():
                # END LOOP（先 END 后 LOOP）
                if body[k:k + 3].upper() == "END" and (k + 3 >= length or not body[k + 3].isalnum()):
                    nx = k + 3
                    while nx < length and body[nx].isspace():
                        nx += 1
                    if body[nx:nx + 4].upper() == "LOOP" and (nx + 4 >= length or not body[nx + 4].isalnum()):
                        loop_depth -= 1
                        k = nx + 4
                        # 吃掉随后的 ;
                        while k < length and body[k].isspace():
                            k += 1
                        if k < length and body[k] == ";":
                            k += 1
                        continue
                # 嵌套 LOOP：单独 LOOP 关键字也增加 depth
                if body[k:k + 4].upper() == "LOOP" and (k + 4 >= length or not body[k + 4].isalnum()):
                    loop_depth += 1
                    k += 4
                    continue
            k += 1
        scope_end = k
        scopes.append((for_start, scope_end, cursor_sources))
        # 同一外层 LOOP 内部还可能嵌另一层 cursor FOR；从 LOOP 关键字后继续扫
        pos = j + 4
    return scopes


def _strip_cursor_for_prefix(seg_text: str) -> str:
    """老接口：仅返回剥掉 cursor 头部后的文本（不带 source 提取）。"""
    return _strip_cursor_for_prefix_with_sources(seg_text)[0]


# S5：剥 cursor FOR 头部时，顺便从 cursor SELECT 里抽源表 —— 给 analyzer 做
# `INSERT VALUES (rec.col)` 这种"无 source_tables 的 INSERT"补 source → target
# 边的依据。否则 graph 在 cursor loop 体内会断链。
_RE_FROM_TABLE = re.compile(
    r"\bFROM\s+([\w$#.\"`\[\]]+(?:\s*,\s*[\w$#.\"`\[\]]+)*)",
    flags=re.IGNORECASE,
)
_RE_JOIN_TABLE = re.compile(
    r"\bJOIN\s+([\w$#.\"`\[\]]+)",
    flags=re.IGNORECASE,
)


def _extract_cursor_select_tables(cursor_select: str) -> list[str]:
    """轻量级提取 cursor SELECT 子查询的源表。不走 sqlglot —— 段内的 SELECT 可能
    嵌套子查询、CTE，sqlglot 全量解析成本高且这里只需"哪些表名出现"的近似列表。

    仅取顶层 FROM / JOIN 后面的标识符；剥引号 / schema.table 保留点号。
    """
    tables: list[str] = []
    seen: set[str] = set()

    def _push(raw: str) -> None:
        name = raw.strip().strip('"`[]')
        # 跳过括号子查询、保留字、空名
        if not name or name.startswith("(") or name.upper() in {"WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "FETCH"}:
            return
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        tables.append(name)

    for m in _RE_FROM_TABLE.finditer(cursor_select):
        # FROM a, b, c —— 多表 comma 分隔
        for piece in m.group(1).split(","):
            _push(piece.split()[0] if piece.split() else "")
    for m in _RE_JOIN_TABLE.finditer(cursor_select):
        _push(m.group(1))
    return tables


def _strip_cursor_for_prefix_with_sources(seg_text: str) -> tuple[str, list[str]]:
    """如果 seg 以 `FOR x IN (...) LOOP` 开头，把头部剥掉，返回 `(LOOP 后内容, cursor 源表列表)`。
    其他情况返回 `(原 seg_text, [])`。剥掉时同步括号配平 —— cursor 子查询里可能含括号。
    """
    if not _RE_CURSOR_FOR_LOOP_HEAD.match(seg_text):
        return seg_text, []
    # 找首个出现在括号外的 LOOP 关键字。同时记录最外层 ( ... ) 的内容（cursor SELECT）
    depth = 0
    pos = 0
    length = len(seg_text)
    cursor_select_start = -1   # 第一次 depth 由 0 → 1 的位置（左括号后）
    cursor_select_end = -1     # depth 由 1 → 0 的位置（右括号前）
    while pos < length:
        ch = seg_text[pos]
        if ch in "'\"":
            quote = ch
            pos += 1
            while pos < length:
                if seg_text[pos] == quote and (pos + 1 >= length or seg_text[pos + 1] != quote):
                    pos += 1
                    break
                pos += 1
            continue
        if ch == "(":
            if depth == 0:
                cursor_select_start = pos + 1
            depth += 1
            pos += 1
            continue
        if ch == ")":
            depth -= 1
            if depth == 0 and cursor_select_end == -1:
                cursor_select_end = pos
            pos += 1
            continue
        if depth == 0 and ch.isalpha():
            m = _RE_LOOP_KEYWORD.match(seg_text, pos)
            if m and (m.end() >= length or not seg_text[m.end()].isalnum()):
                cursor_sources: list[str] = []
                if 0 <= cursor_select_start < cursor_select_end:
                    cursor_sources = _extract_cursor_select_tables(
                        seg_text[cursor_select_start:cursor_select_end]
                    )
                return seg_text[m.end():].lstrip(), cursor_sources
        pos += 1
    return seg_text, []
# 纯空白 + 行注释（`--`） + 块注释（`/* */`）的组合。fullmatch 这个表示 DML 前面
# 没有控制流壳子（IF/THEN、CASE 等），可以原样保留——业务标题就在前缀注释里。
_RE_PURE_COMMENT_PREFIX = re.compile(r"(?:\s|--[^\n]*|/\*(?:[^*]|\*(?!/))*\*/)*")


def extract_procedure_segments(sql: str) -> list[dict[str, Any]]:
    """Extract DML statements nested inside CREATE PROCEDURE/FUNCTION/PACKAGE BODY/TRIGGER blocks.

    Skips control-flow shells (IF/LOOP/EXCEPTION) and nested BEGIN/END blocks via token-balanced scan.

    Important: 段落保留原始换行——`-- 行注释` 必须靠换行终止才不会把后面的列名一起吞掉。
    用规范化文本（空白压平）做 dedupe key，原始格式喂给 sqlglot 解析。

    每条记录包含：procedure_name / procedure_kind / segment_index / sql / confidence /
    line_start / line_end / preceding_comment / parse_status。parse_status 默认 'unknown'，
    由上游（analyzer.py）在跑完 sqlglot 后回填 'parsed' / 'unsupported'。
    """
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    # S5 PR13：把 PACKAGE BODY 拆成多个嵌套 procedure scope，每个独立处理。
    # 旧逻辑只看第一个 BEGIN 到匹配 END，会漏掉同一 PACKAGE BODY 里的第二个
    # / 后续 PROCEDURE。
    for outer_header in _RE_PROC_HEADER.finditer(sql):
        outer_name = outer_header.group("name").strip()
        outer_kind = " ".join(outer_header.group("kind").split()).upper()
        if outer_kind == "PACKAGE BODY":
            # PACKAGE BODY 自身没有 BEGIN/END 配对（IS/AS ... END pkg; 直接收尾）。
            # 简化：范围从 header 到下一个 CREATE PROCEDURE/FUNCTION/PACKAGE 或文件末尾。
            next_outer = _RE_PROC_HEADER.search(sql, outer_header.end())
            pkg_body_end = next_outer.start() if next_outer else len(sql)
            # 包级声明区：header 后到第一个嵌套 PROCEDURE/FUNCTION 之前
            first_nested = _RE_NESTED_PROC_HEADER.search(sql, outer_header.end(), pkg_body_end)
            pkg_decl_end = first_nested.start() if first_nested else pkg_body_end
            pkg_declaration_region = sql[outer_header.end():pkg_decl_end]
            # 找所有嵌套 PROCEDURE/FUNCTION
            scopes = _find_nested_proc_scopes(sql, outer_header.end(), pkg_body_end, outer_name)
            if not scopes:
                # 没嵌套 → 包体本身有 BEGIN 段（rare），按 outer_name 处理
                scopes = [(outer_name, outer_kind, outer_header.end(), pkg_body_end, pkg_declaration_region)]
        else:
            # 普通 PROCEDURE / FUNCTION / TRIGGER：单个 scope
            body_start = _RE_BEGIN.search(sql, pos=outer_header.end())
            if not body_start:
                continue
            _, body_end = _find_block_end(sql, body_start.end(), depth=1)
            scopes = [(outer_name, outer_kind, outer_header.end(), body_end, sql[outer_header.end():body_start.start()])]
            # 用 declaration_region = header 到 BEGIN 之间
            scopes = [(outer_name, outer_kind, body_start.end(), body_end, sql[outer_header.end():body_start.start()])]

        for name, kind, scope_body_start, scope_body_end, declaration_region in scopes:
            # PR13：包级 cursor 声明也合并进每个嵌套 proc 的 declaration_region
            if outer_kind == "PACKAGE BODY":
                merged_decl = pkg_declaration_region + "\n" + declaration_region
            else:
                merged_decl = declaration_region
            body = sql[scope_body_start:scope_body_end]
            body_offset = scope_body_start
            for index, item in enumerate(_iter_procedure_body_segments(body, merged_decl), start=1):
                preserved = clean_procedure_segment(item.text)
                if not preserved:
                    continue
                dedupe_key = " ".join(preserved.split())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                abs_start = body_offset + item.dml_start
                abs_end = body_offset + item.end
                result.append(
                    {
                        "procedure_name": name,
                        "procedure_kind": kind,
                        "segment_index": str(index),
                        "sql": preserved,
                        "confidence": "high",
                        "line_start": _line_of(sql, abs_start),
                        "line_end": _line_of(sql, abs_end),
                        "preceding_comment": item.preceding_comment,
                        "parse_status": "unknown",
                        # S5：cursor FOR 段才有，普通段是空 list
                        "cursor_sources": list(item.cursor_sources or []),
                    }
                )
    return result


# S5 PR13：辅助函数 ─────────────────────────────────────────────────────────


def _find_block_end(sql: str, start_pos: int, depth: int = 0) -> tuple[int, int]:
    """从 start_pos 开始找 PL/SQL 块的结束位置，返回 (matched_begin_pos, end_pos)。

    depth=0 时，调用方在 IS/AS 之后；先要 skip 到第一个 BEGIN，然后配对 END。
    depth=1 时，已经 skip 过 BEGIN 了，直接配对 END。
    """
    if depth == 0:
        begin_match = _RE_BEGIN.search(sql, pos=start_pos)
        if not begin_match:
            return -1, len(sql)
        matched_begin = begin_match.start()
        cursor = begin_match.end()
        depth = 1
    else:
        matched_begin = start_pos
        cursor = start_pos
    for tok_match in _RE_BLOCK_TOKEN.finditer(sql, pos=cursor):
        tok = tok_match.group(0).upper()
        if tok == "BEGIN":
            depth += 1
        else:
            if not _is_block_end(sql, tok_match.end()):
                continue
            depth -= 1
            if depth == 0:
                return matched_begin, tok_match.start()
    return matched_begin, len(sql)


def _find_nested_proc_scopes(sql: str, start: int, end: int, parent_name: str) -> list[tuple[str, str, int, int, str]]:
    """扫 PACKAGE BODY 范围内所有嵌套 PROCEDURE/FUNCTION，返回每个的
    (qualified_name, kind, body_start_offset, body_end_offset, declaration_region)。

    每个嵌套 proc 用其自己的 IS/AS → BEGIN 之间作 declaration_region，与 PACKAGE
    BODY 包级声明区合并使用。
    """
    out: list[tuple[str, str, int, int, str]] = []
    pos = start
    while pos < end:
        m = _RE_NESTED_PROC_HEADER.search(sql, pos, end)
        if not m:
            break
        kind = m.group("kind").upper()
        local_name = m.group("name").strip()
        # 找 IS|AS 之后的 BEGIN（同时可能跨过参数列表 / RETURN type 子句）
        body_start_match = _RE_BEGIN.search(sql, m.end(), end)
        if not body_start_match:
            pos = m.end()
            continue
        scope_decl_region = sql[m.end():body_start_match.start()]
        body_begin, body_end = _find_block_end(sql, body_start_match.end(), depth=1)
        if body_end > end:
            body_end = end
        qualified = f"{parent_name}.{local_name}" if parent_name and local_name not in parent_name else local_name
        out.append((qualified, kind, body_start_match.end(), body_end, scope_decl_region))
        pos = body_end + 1
    return out


def _line_of(text: str, offset: int) -> int:
    """1-based line number of `offset` in `text`. Beyond-end → last line."""
    if offset <= 0:
        return 1
    capped = min(offset, len(text))
    return text.count("\n", 0, capped) + 1


def clean_procedure_segment(segment: str) -> str:
    """Strip leading/trailing whitespace and the trailing `;`, but **keep** newlines.

    `-- line comments` 必须靠换行终止；如果把所有空白压成单空格，注释会一直吞到行末，
    导致 sqlglot 把列名 / 表达式都识别成注释、括号失配。所以过程体段不能用
    `clean_dynamic_segment`（它是给单行字符串字面量用的）。
    """
    return segment.strip().rstrip(";").strip()


def _iter_procedure_body_segments(body: str, declaration_region: str = "") -> list[_BodySegment]:
    """Split a procedure body into top-level statements, skipping control-flow shells.

    返回带 body-内偏移和前置注释的 _BodySegment，方便上层算 line_start/line_end。

    S5 PR7：declaration_region 是过程头部 `IS/AS ... BEGIN` 之间的区域文本，
    用于抽显式 CURSOR 声明（`CURSOR cur_x IS SELECT ...;`）。下面 LOOP 范围
    扫描时 `FOR rec IN cur_x LOOP` 形式靠这个映射回填 cursor_sources。
    """
    raw_segments: list[tuple[str, int, int]] = []  # (text, body_start, body_end_inclusive_semicolon)
    depth = 0
    buffer: list[str] = []
    seg_start = 0
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
            raw_segments.append(("".join(buffer), seg_start, pos + 1))
            buffer = []
            pos += 1
            seg_start = pos
            continue
        buffer.append(char)
        pos += 1
    tail = "".join(buffer)
    if tail.strip():
        raw_segments.append((tail, seg_start, length))

    cleaned: list[_BodySegment] = []
    for raw_text, raw_start, raw_end in raw_segments:
        # raw_start 指向缓冲起点（可能含前导空白）；找到首个非空白字符作为段起点
        leading_ws = len(raw_text) - len(raw_text.lstrip())
        seg_text_orig = raw_text.strip()
        if not seg_text_orig:
            continue
        # The segment may start with control-flow shells (IF ... THEN, ELSIF ... THEN, FOR ... LOOP).
        # Find the first DML keyword and analyze from there.
        # 特殊处理：`FOR rec IN (SELECT ...) LOOP <DML>` —— cursor 子查询里的 SELECT
        # 不是顶层 DML，剥掉 cursor 头部，从 LOOP 后开始找。
        # S5：同时抽出 cursor SELECT 引用的源表，挂到 _BodySegment.cursor_sources，
        # 让 analyzer 给 body 里的 INSERT VALUES (rec.col) 补 source → target 边
        seg_text, cursor_sources = _strip_cursor_for_prefix_with_sources(seg_text_orig)
        cursor_strip = len(seg_text_orig) - len(seg_text)
        match = _RE_BODY_DML.search(seg_text)
        if not match:
            continue
        prefix = seg_text[:match.start()]
        # 如果 DML 前面只有空白和 line/block 注释（业务标题 `-- 集中交易`），保留 ——
        # sqlglot 会把它挂到 statement.comments 上做 statement_title。
        # 反之（IF/THEN、CASE 之类的控制流壳子 / cursor LOOP），仍然剥掉。
        if _RE_PURE_COMMENT_PREFIX.fullmatch(prefix):
            kept_text = seg_text
            preceding_comment = _strip_comment_prefix(prefix)
        else:
            kept_text = seg_text[match.start():]
            preceding_comment = ""
        # body 内偏移：raw_start + 前导空白 + cursor 头部长度 + kept_text 在剥后 seg_text 中的起点
        kept_offset_in_seg = len(seg_text) - len(kept_text)
        body_kept_start = raw_start + leading_ws + cursor_strip + kept_offset_in_seg
        if preceding_comment:
            dml_offset = raw_start + leading_ws + cursor_strip + match.start()
        else:
            dml_offset = body_kept_start
        cleaned.append(_BodySegment(
            text=kept_text.strip(),
            dml_start=dml_offset,
            end=raw_end,
            preceding_comment=preceding_comment,
            cursor_sources=cursor_sources,
        ))
    # S5 PR2：扫整个 body 找 cursor FOR LOOP 范围，给已 split 出来但落在 LOOP 体内
    # 的多 DML 段补 cursor_sources。比如：
    #   FOR rec IN (SELECT FROM ods.t) LOOP
    #     INSERT INTO dwd.t1 VALUES (rec.x);   -- 段0：原本就有 cursor_sources
    #     UPDATE dwd.t2 SET x=rec.x WHERE ...; -- 段1：被 ; 切出来时丢了上下文
    #   END LOOP;
    # 取最内层 scope（嵌套时最后匹配到的）。
    # PR7：cursor_decls 从 declaration_region 抽，让 LOOP 扫描能解析 `FOR rec IN cur_x LOOP`
    extra_decls = _collect_cursor_decls(declaration_region) if declaration_region else {}
    scopes = _collect_loop_scopes(body, extra_decls)
    if scopes:
        for seg in cleaned:
            if seg.cursor_sources:
                continue
            inherited: list[str] = []
            for scope_start, scope_end, scope_sources in scopes:
                if scope_start <= seg.dml_start < scope_end and scope_sources:
                    inherited = scope_sources
            if inherited:
                seg.cursor_sources = list(inherited)
    return cleaned


_RE_LINE_COMMENT = re.compile(r"--[^\n]*")
_RE_BLOCK_COMMENT = re.compile(r"/\*(?:[^*]|\*(?!/))*\*/", flags=re.DOTALL)


def _strip_comment_prefix(prefix: str) -> str:
    """从一段纯注释前缀里抽业务标题：剥 `--` / `/* */` 标记，多行用空格连。

    截断 200 字符，避免巨型 banner 注释把 UI 撑爆。
    """
    pieces: list[str] = []
    for m in _RE_BLOCK_COMMENT.finditer(prefix):
        body = m.group(0)[2:-2].strip().strip("*").strip()
        if body:
            pieces.append(body)
    # 块注释剥掉后，剩下的是行注释和空白
    line_only = _RE_BLOCK_COMMENT.sub(" ", prefix)
    for m in _RE_LINE_COMMENT.finditer(line_only):
        body = m.group(0).lstrip("-").strip()
        if body:
            pieces.append(body)
    if not pieces:
        return ""
    joined = " ".join(pieces).strip()
    if len(joined) > 200:
        joined = joined[:200].rstrip() + "…"
    return joined


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
    unresolved_seen: set[str] = set()
    for match in immediate_var_pattern.finditer(sql):
        var_name = match.group(1).lower()
        if var_name in plsql_vars:
            for assignment in plsql_vars[var_name]:
                add(assignment["sql"], "var_concat", assignment["confidence"])
        elif var_name not in unresolved_seen:
            # 变量来自过程参数 / 包变量 / cursor / 外部传入，脚本里没赋值——
            # 静态分析无法推断 SQL 内容。出占位段触发 dynamic_sql warning，
            # confidence='unresolved'。下游 sqlglot 会忽略 (ignore_errors=True)。
            unresolved_seen.add(var_name)
            result.append({
                "sql": f"-- unresolved EXECUTE IMMEDIATE variable: {match.group(1)}",
                "source": "execute_var_unresolved",
                "confidence": "unresolved",
            })

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
