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


def normalize_for_parsing(sql: str) -> str:
    """把 SQL 转成 sqlglot 可吞的形式。

    - code 段：全角标点 → 半角；`${name}` → `:name`；比较运算符空白合并
    - string / line_comment / block_comment：原样保留

    保证不增减字符总长，所以原文件的行号在 normalized 文本里仍然对应。
    """
    if not sql:
        return sql
    out: list[str] = []
    for kind, text in _walk_sql(sql):
        if kind == "code":
            text = _replace_fullwidth(text)
            text = _RE_TEMPLATE_VAR.sub(lambda m: ":" + m.group(1), text)
            text = _normalize_operator_spacing(text)
        out.append(text)
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
