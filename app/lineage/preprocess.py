"""SQL 解析前的归一化。

为什么需要：
1. ETL 工具 / 中文输入法生成的 SQL 经常混入全角标点（`（` U+FF08、`）` U+FF09、
   `，` U+FF0C、`；` U+FF1B），sqlglot 不识别。常见 case：`t.l_class in（'1','13')`
   一处全角左括号让整段 CASE WHEN 报 `Expected END after CASE`。
2. 模板变量 `${data_dt1}` 是 ETL 引擎的占位语法，sqlglot 识别不了，会触发
   parse error。需要替换成 sqlglot 能吃的 bind param 形式 `:data_dt1`。

实现要点：
- 状态机扫描，区分 code / string / line_comment / block_comment 四种段
- 只对 code 段做替换，避免破坏字符串字面量内的全角符（用户可能在 `'a，b'`
  里故意放全角，那是数据，不动）
- 替换 1:1 等长字符（全角符）或同行内压缩（`${name}` → `:name`），不增减换行，
  所以 procedure_segments 的 line_start/line_end 不受影响
"""
from __future__ import annotations

import re
from collections.abc import Iterator


# 已确认会撞 sqlglot 的几个全角标点，1:1 等宽替换。
_FULLWIDTH_PUNCT_MAP = {
    "（": "(",   # （
    "）": ")",   # ）
    "，": ",",   # ，
    "；": ";",   # ；
}


_RE_TEMPLATE_VAR = re.compile(r"\$\{\s*([A-Za-z_][\w$#]*)\s*\}")

# DM / Oracle 容许比较运算符之间出现空白（`< =`、`> =`、`! =`、`< >`），
# sqlglot 把它们当成两个独立 token 报 `Required keyword: 'expression' missing`。
# 在 code 段做归一化（不动 string 字面量、不动注释；不增减字符总长保证行号）。
_RE_OPERATOR_SPACING = re.compile(r"(?<=[\w\)\s'\"`\]])([<>!])\s+([=>])(?=[\w\(\s'\"`\[])")

# 部分 sqlglot 版本 / 方言组合对 `DELETE FROM table AS alias ...` 的处理不稳定。
# Oracle/DM/MySQL 都能接受不带 AS 的删除别名形式；这里仅把 AS 替换成等长空白，
# 保留 alias 和行列位置，避免后续表名 / 谓词分析偏移。
_RE_DELETE_AS_ALIAS = re.compile(
    r"(?ix)"
    r"\bDELETE\s+FROM\s+"
    r"(?P<table>(?:[A-Za-z_][\w$#]*|\"[^\"]+\"|`[^`]+`|\[[^\]]+\])"
    r"(?:\s*\.\s*(?:[A-Za-z_][\w$#]*|\"[^\"]+\"|`[^`]+`|\[[^\]]+\]))*)"
    r"(?P<ws>\s+)AS(?P<after>\s+[A-Za-z_][\w$#]*)"
    r"(?=\s*(?:WHERE|USING|RETURNING|ORDER\s+BY|LIMIT|;|$))",
)

# S5 PR20：Oracle PL/SQL `RETURNING ... INTO :var, :var2` 子句让 sqlglot 报
# Invalid expression。剥掉这个尾巴（保留分号），不影响表/列血缘 —— 数据流向
# 由 INSERT/UPDATE/DELETE 主体决定。
_RE_RETURNING_INTO = re.compile(
    r"\bRETURNING\b[^;]*?\bINTO\b[^;]*?(?=;|$)",
    flags=re.IGNORECASE,
)


def normalize_for_parsing(sql: str) -> str:
    """把 SQL 转成 sqlglot 可吞的形式。

    - code 段：全角标点 → 半角；`${name}` → `:name`；比较运算符空白合并
    - string / line_comment / block_comment：原样保留

    保证不增减字符总长，所以原文件的行号在 normalized 文本里仍然对应。
    """
    if not sql:
        return sql
    # 第 1 步：INSERT alias-prefixed 列名归一化必须用整段 SQL（不能走 _walk_sql 切片，
    # 因为 INSERT 列表跨行 + 内嵌行注释会把 code 段切碎，regex 找不到匹配的 `)`）。
    # 这个函数自带 string / comment 状态机，自己跳过它们。
    sql = _normalize_insert_alias_prefix(sql)

    # 第 2 步：剩余的 code 段内归一化（全角 / 模板变量 / 运算符空白都是单 token 替换，
    # 切片不影响正确性）
    out: list[str] = []
    for kind, text in _walk_sql(sql):
        if kind == "code":
            text = _replace_fullwidth(text)
            text = _RE_TEMPLATE_VAR.sub(lambda m: ":" + m.group(1), text)
            text = _normalize_operator_spacing(text)
            text = _normalize_delete_as_alias(text)
            text = _strip_returning_into(text)
        out.append(text)
    return "".join(out)


def _strip_returning_into(text: str) -> str:
    """PR20：Oracle PL/SQL `RETURNING col INTO :var` —— sqlglot 不接受。
    剥掉这段尾巴，行号通过等长空格替换保持。"""
    if "returning" not in text.lower():
        return text
    def _replace(match: "re.Match[str]") -> str:
        return " " * (match.end() - match.start())
    return _RE_RETURNING_INTO.sub(_replace, text)


def _normalize_delete_as_alias(text: str) -> str:
    """`DELETE FROM tbl AS t` -> `DELETE FROM tbl    t`，等长替换 AS。"""
    if "delete" not in text.lower() or " as " not in text.lower():
        return text

    def _replace(match: "re.Match[str]") -> str:
        return (
            match.group(0)[: match.start("ws") - match.start(0)]
            + match.group("ws")
            + "  "
            + match.group("after")
        )

    return _RE_DELETE_AS_ALIAS.sub(_replace, text)


def _normalize_insert_alias_prefix(text: str) -> str:
    """`INSERT INTO tbl alias (alias.col1, alias.col2, ...)` → 标准形式。

    Oracle / DM 容许 INSERT 列表用 alias-prefixed 列名（如 `c.customer_no`），
    sqlglot 拒绝（要求列表里只能是 bare column）。
    修法：扫每个 INSERT INTO 子句：如果表名后面跟一个标识符（alias），且后面紧跟
    `(...)` 列表，就在列表内把 `<alias>.` 前缀剥掉，alias 自身保留（位置等长，
    不偏行号）。
    """
    if "insert" not in text.lower():
        return text

    pattern = re.compile(
        r"(?ix)"
        r"\b(insert\s+into\s+[\w$#.\"`\[\]]+)"   # group 1: INSERT INTO schema.table
        r"(\s+)([A-Za-z_][\w$#]*)"                # group 2: ws, group 3: alias
        r"(\s*\(\s*)",                            # group 4: opening (
    )

    def _rewrite(m: "re.Match[str]") -> str:
        head = m.group(0)
        alias = m.group(3)
        # alias 不能是 SQL 关键字（误判 INSERT INTO t SELECT 等）
        if alias.upper() in {"SELECT", "VALUES", "WITH", "AS", "DEFAULT"}:
            return head
        # 找匹配的右括号
        start = m.end()
        depth = 1
        i = start
        in_str = False
        escape = False
        quote = ""
        in_line_comment = False
        in_block_comment = False
        while i < len(text):
            ch = text[i]
            if escape:
                escape = False
                i += 1
                continue
            if in_line_comment:
                if ch == "\n":
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                if ch == "*" and i + 1 < len(text) and text[i + 1] == "/":
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue
            if in_str:
                if ch == "\\":
                    escape = True
                elif ch == quote:
                    if i + 1 < len(text) and text[i + 1] == quote:
                        i += 2  # SQL 双引号转义
                        continue
                    in_str = False
                i += 1
                continue
            if ch == "-" and i + 1 < len(text) and text[i + 1] == "-":
                in_line_comment = True
                i += 2
                continue
            if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
                in_block_comment = True
                i += 2
                continue
            if ch in ("'", '"', "`"):
                in_str = True
                quote = ch
                i += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if depth != 0:
            return head
        # text[start:i] 是 (...) 内部内容
        body = text[start:i]
        # 剥 <alias>. 前缀（保留长度：换成等量空格）
        alias_re = re.compile(r"\b" + re.escape(alias) + r"\.", flags=re.IGNORECASE)
        new_body = alias_re.sub(lambda mm: " " * (len(alias) + 1), body)
        # head 已经覆盖到 start；保持原顺序输出
        return head + new_body + ")"

    # 用回调替换：因为我们要扫到匹配的右括号，sub 一次只能动 head 部分
    # 改成手工遍历
    out: list[str] = []
    last_end = 0
    for m in pattern.finditer(text):
        if m.start() < last_end:
            continue
        head = m.group(0)
        alias = m.group(3)
        if alias.upper() in {"SELECT", "VALUES", "WITH", "AS", "DEFAULT"}:
            continue  # 跳过假阳性
        start = m.end()
        depth = 1
        i = start
        in_str = False
        escape = False
        quote = ""
        in_line_comment = False
        in_block_comment = False
        while i < len(text):
            ch = text[i]
            if escape:
                escape = False
                i += 1
                continue
            if in_line_comment:
                if ch == "\n":
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                if ch == "*" and i + 1 < len(text) and text[i + 1] == "/":
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue
            if in_str:
                if ch == "\\":
                    escape = True
                elif ch == quote:
                    if i + 1 < len(text) and text[i + 1] == quote:
                        i += 2
                        continue
                    in_str = False
                i += 1
                continue
            if ch == "-" and i + 1 < len(text) and text[i + 1] == "-":
                in_line_comment = True
                i += 2
                continue
            if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
                in_block_comment = True
                i += 2
                continue
            if ch in ("'", '"', "`"):
                in_str = True
                quote = ch
                i += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if depth != 0:
            continue
        # 把上一段 + head + 处理过的 body 全 append
        out.append(text[last_end:m.start()])
        out.append(head)
        body = text[start:i]
        alias_re = re.compile(r"\b" + re.escape(alias) + r"\.", flags=re.IGNORECASE)
        new_body = alias_re.sub(lambda mm: " " * (len(alias) + 1), body)
        out.append(new_body)
        out.append(")")
        last_end = i + 1
    out.append(text[last_end:])
    return "".join(out)


def _normalize_operator_spacing(text: str) -> str:
    """`< =` → `<=`（保留长度的归一化：先用空格补回缺的字符）。

    sqlglot 收到 `< =` 会认为是 LT + EQ 两个独立 token，要么报错要么误判。
    DM / Oracle / 部分 ETL 工具的 SQL formatter 会把所有 binary operator 都
    分开打。这里把 `< =`、`> =`、`! =`、`< >` 合并成 `<=` 等，并在原空白位置
    补一个空格，保证整体长度（→ 行号）不变。
    """
    def _merge(match: "re.Match[str]") -> str:
        first, second = match.group(1), match.group(2)
        # 原文 `<` + 1+ 空白 + `=` → `<=` + 等量空格保持长度
        consumed = match.end() - match.start()
        return first + second + " " * (consumed - 2)
    return _RE_OPERATOR_SPACING.sub(_merge, text)


def _replace_fullwidth(text: str) -> str:
    if not any(ch in text for ch in _FULLWIDTH_PUNCT_MAP):
        return text
    return text.translate(str.maketrans(_FULLWIDTH_PUNCT_MAP))


def _walk_sql(sql: str) -> Iterator[tuple[str, str]]:
    """切 SQL 成 (kind, text) 序列。

    kind ∈ {'code', 'string', 'line_comment', 'block_comment'}。
    code 段在调用方决定是否做替换；其他三种段是数据 / 元数据，不能改。

    字符串引号识别：单引号（标准 SQL）/ 双引号（Oracle/DM/PG 标识符引用）。
    转义形式 `''` / `""` 内联。Oracle 的 q'[...]' 暂不支持（罕见，遇到再加）。
    """
    pos = 0
    n = len(sql)
    while pos < n:
        ch = sql[pos]
        # 块注释 /* ... */（含 hint /*+ ... */）
        if ch == "/" and pos + 1 < n and sql[pos + 1] == "*":
            end = sql.find("*/", pos + 2)
            if end == -1:
                yield ("block_comment", sql[pos:])
                return
            yield ("block_comment", sql[pos:end + 2])
            pos = end + 2
            continue
        # 行注释 -- ... \n
        if ch == "-" and pos + 1 < n and sql[pos + 1] == "-":
            nl = sql.find("\n", pos)
            if nl == -1:
                yield ("line_comment", sql[pos:])
                return
            # 行注释不含末尾换行，让换行进 code 段（保持后续的 line_start 正确）
            yield ("line_comment", sql[pos:nl])
            pos = nl
            continue
        # 字符串字面量
        if ch in "'\"":
            text, end_pos = _consume_string(sql, pos, ch)
            yield ("string", text)
            pos = end_pos
            continue
        # code：累积到下一个特殊字符
        start = pos
        while pos < n:
            c = sql[pos]
            if c in "'\"":
                break
            if c == "/" and pos + 1 < n and sql[pos + 1] == "*":
                break
            if c == "-" and pos + 1 < n and sql[pos + 1] == "-":
                break
            pos += 1
        yield ("code", sql[start:pos])


def _consume_string(sql: str, start: int, quote: str) -> tuple[str, int]:
    """从 start 处的引号开始读到匹配的结束引号（含转义 `''` / `""`），
    返回 (字符串内容含引号, 结束后的 pos)。未闭合时把剩余全吞，让 sqlglot 自己报错。"""
    n = len(sql)
    pos = start + 1
    while pos < n:
        if sql[pos] == quote:
            if pos + 1 < n and sql[pos + 1] == quote:
                pos += 2
                continue
            return sql[start:pos + 1], pos + 1
        pos += 1
    return sql[start:], n
