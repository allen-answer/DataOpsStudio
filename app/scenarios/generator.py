"""Scenario generator —— 按 Scenario 产数据(Phase 12 切片 2,Phase 14 P0-2 加 streaming)。

两套 API:
- `generate_scenario(scenario)` 返回 `{table_name: list[row_dict]}`(老路径,小 scenario 仍用)
- `iter_table_rows_streaming(table, ...)` yield batches —— Phase 14 加,内存 O(batch × col_width)
  恒定,千万行不爆。`materialize_streaming` 调用方。

派生表(derives_from)在 streaming 模式下走 SQL `INSERT INTO derived SELECT FROM source`,
不再 Python 内存复制源行 —— 详见 materializer.materialize_streaming。

anomaly 处理(streaming):
- row-level(value_drift / null_drift / type_mismatch):预采样 hit 索引 + inline 应用
- set-level(missing_rows):预采样跳过索引,生成阶段直接漏掉
- set-level(extra_rows / duplicate_pk):base 行跑完后在 final batch 追加

seed != 0 时复跑同结果。seed=0 时不固定 RNG。
"""
from __future__ import annotations

import random
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterator

from app.scenarios.models import (
    AnomalyDef,
    ColumnDef,
    Scenario,
    TableDef,
)


Row = dict[str, Any]
TableData = list[Row]


def generate_scenario(scenario: Scenario) -> dict[str, TableData]:
    """Generate all tables → {table_name: rows}.

    Phase 14 #3 Round 6:顺序由 derives_from + FK references 共同决定。
    被引用表先生成,把列值入 FK pool,引用表 gen=foreign_key 时从 pool 抽样
    → JOIN 拿到匹配行。
    """
    rng = random.Random(scenario.seed) if scenario.seed else random.Random()
    all_tables = {t.name: t for t in scenario.tables}
    out: dict[str, TableData] = {}
    pool = _FKPool()
    for table in _resolve_order(scenario.tables):
        out[table.name] = _generate_table(table, all_tables, out, rng, pool)
        _populate_pool(pool, table, out[table.name])
    for anomaly in scenario.anomalies:
        _apply_anomaly(out, anomaly, all_tables, rng)
    return out


# ─── FK pool(Phase 14 #3 Round 6)────────────────────────────────────────


class _FKPool:
    """跨表共享值池。key=(table_name, column_name) → 已生成的所有值列表。

    支持 schema-qualified 表名查找(`ods.foo` 也能用 `foo` 找到)。
    """
    def __init__(self) -> None:
        self._pool: dict[tuple[str, str], list[Any]] = {}

    def add(self, table_name: str, column_name: str, values: list[Any]) -> None:
        self._pool[(table_name, column_name)] = values

    def get(self, references: str) -> list[Any] | None:
        """references 格式 "table.col" 或 "schema.table.col"。
        先精确匹配,再去 schema 前缀匹配。找不到返 None。"""
        if "." not in references:
            return None
        ref_table, ref_col = references.rsplit(".", 1)
        if (ref_table, ref_col) in self._pool:
            return self._pool[(ref_table, ref_col)]
        # fallback:去 schema 前缀
        if "." in ref_table:
            base = ref_table.rsplit(".", 1)[1]
            if (base, ref_col) in self._pool:
                return self._pool[(base, ref_col)]
        # fallback:加 schema 前缀(被引用表注册的是裸名,引用的是 schema.table)
        for (t, c), vals in self._pool.items():
            if c == ref_col and (t == ref_table or t.endswith("." + ref_table)):
                return vals
        return None


def _populate_pool(pool: _FKPool, table: TableDef, rows: TableData) -> None:
    """把刚生成的表的所有列值入 pool(被 FK 引用了才会被查到,简单起见全入)。

    内存代价:O(rows × columns)。1500w 行 × 15 列 ≈ 200M strings,占 1-2GB。
    实战中只有"被引用"的 PK 列才会被查,其它列填进去其实可不入 — 留 P1 优化。
    """
    for col in table.columns:
        values = [row.get(col.name) for row in rows]
        pool.add(table.name, col.name, values)


# ─── table-level ────────────────────────────────────────────────────────────


def _resolve_order(tables: list[TableDef]) -> list[TableDef]:
    """拓扑序:derives_from 父表 + FK references 被引用表 → 先生成。

    Phase 14 #3 Round 6:除 derives_from 外,新增 FK 依赖。
    Scenario.model_validator 已检测循环,这里走 DFS 假设是 DAG。
    """
    by_name = {t.name: t for t in tables}
    # 支持 schema.table 引用裸表名查找
    by_simple: dict[str, TableDef] = {}
    for t in tables:
        simple = t.name.rsplit(".", 1)[-1] if "." in t.name else t.name
        by_simple.setdefault(simple, t)

    def find_table(ref: str) -> TableDef | None:
        if ref in by_name:
            return by_name[ref]
        simple = ref.rsplit(".", 1)[-1] if "." in ref else ref
        return by_simple.get(simple)

    seen: set[str] = set()
    order: list[TableDef] = []

    def visit(t: TableDef) -> None:
        if t.name in seen:
            return
        # 1. derives_from 父表先
        if t.derives_from:
            parent = find_table(t.derives_from)
            if parent and parent.name != t.name:
                visit(parent)
        # 2. FK references 被引用表先
        for col in t.columns:
            if col.gen == "foreign_key" and col.references:
                ref_table_path = col.references.rsplit(".", 1)[0]
                parent = find_table(ref_table_path)
                if parent and parent.name != t.name:
                    visit(parent)
        seen.add(t.name)
        order.append(t)

    for t in tables:
        visit(t)
    return order


def _generate_table(
    table: TableDef,
    all_tables: dict[str, TableDef],
    generated: dict[str, TableData],
    rng: random.Random,
    pool: _FKPool | None = None,
) -> TableData:
    if table.derives_from and table.derives_from in generated:
        return _derive_from(table, generated[table.derives_from])
    # Phase 14 #3 Round 6:fk_unique 列要全表去重 → 用 per-table seen set
    unique_seen: dict[str, set[Any]] = {
        c.name: set() for c in table.columns if c.gen == "foreign_key" and c.fk_unique
    }
    return [
        {c.name: _generate_value(c, rng, i, pool, unique_seen) for c in table.columns}
        for i in range(table.rows)
    ]


def _derive_from(table: TableDef, source_rows: TableData) -> TableData:
    overrides = {ov.from_: ov for ov in table.column_overrides}
    rows: TableData = []
    for src in source_rows:
        new: Row = {}
        for col, val in src.items():
            ov = overrides.get(col)
            if ov:
                key = ov.rename or col
                new[key] = _apply_transform(ov.transform, val)
            else:
                new[col] = val
        rows.append(new)
    if table.rows and table.rows < len(rows):
        return rows[: table.rows]
    return rows


# ─── per-column generator ───────────────────────────────────────────────────


def _generate_value(
    col: ColumnDef,
    rng: random.Random,
    row_index: int,
    pool: "_FKPool | None" = None,
    unique_seen: dict[str, set[Any]] | None = None,
) -> Any:
    g = col.gen
    if g == "uuid_short":
        return _uuid_short(rng)
    if g == "random_int":
        lo_raw, hi_raw = (col.range or [0, 1000])
        lo, hi = int(lo_raw), int(hi_raw)
        if col.distribution == "zipf" and col.zipf_alpha:
            return _zipf_int(rng, lo, hi, float(col.zipf_alpha))
        return rng.randint(lo, hi)
    if g == "timestamp":
        lo_raw, hi_raw = col.range or ["2026-01-01", "2026-12-31"]
        start = _parse_dt(lo_raw)
        end = _parse_dt(hi_raw)
        span = max((end - start).total_seconds(), 1.0)
        return start + timedelta(seconds=rng.uniform(0, span))
    if g == "enum":
        if not col.values:
            return None
        if isinstance(col.distribution, list) and col.distribution:
            n = min(len(col.distribution), len(col.values))
            return rng.choices(col.values[:n], weights=col.distribution[:n], k=1)[0]
        return rng.choice(col.values)
    if g == "constant":
        return (col.values or col.range or [None])[0]
    if g == "sequence":
        prefix = (col.values[0] if col.values else "")
        return f"{prefix}{row_index + 1}" if prefix else row_index + 1
    if g == "realistic":
        return _realistic_value(col, rng)
    if g == "foreign_key":
        return _fk_value(col, rng, pool, unique_seen)
    raise ValueError(f"unknown generator: {g}")


def _fk_value(
    col: ColumnDef,
    rng: random.Random,
    pool: "_FKPool | None",
    unique_seen: dict[str, set[Any]] | None,
) -> Any:
    """Phase 14 #3 Round 6:gen=foreign_key 从 pool 抽样。

    - pool 为空 / 池没值 → 返 None(警告交由 generator 层日志)
    - match_rate < 1.0:按概率给"池外"值,模拟脏数据 / LEFT JOIN miss
    - fk_unique=True:per-table 去重抽(最多 50 次尝试,实在不行 fallback 接受重复)
    - fk_distribution=zipf:头部值更可能被抽中(头部账户/客户特征)
    """
    if pool is None or not col.references:
        return None
    candidates = pool.get(col.references)
    if not candidates:
        return None

    # match_rate < 1:部分给池外值
    if col.match_rate < 1.0 and rng.random() > col.match_rate:
        return _generate_unmatched_value(col, rng)

    # fk_unique:per-table 去重抽
    if col.fk_unique and unique_seen is not None:
        seen = unique_seen.get(col.name, set())
        for _ in range(50):
            v = _fk_pick_one(candidates, rng, col)
            if v not in seen:
                seen.add(v)
                return v
        # 50 次都重复 → fallback 接受重复(候选池可能耗尽)
        return _fk_pick_one(candidates, rng, col)

    return _fk_pick_one(candidates, rng, col)


def _fk_pick_one(candidates: list[Any], rng: random.Random, col: ColumnDef) -> Any:
    """按 fk_distribution 从 candidates 抽 1 个。"""
    if col.fk_distribution == "zipf":
        n = len(candidates)
        alpha = float(col.fk_zipf_alpha or 1.2)
        weights = [1.0 / ((i + 1) ** alpha) for i in range(n)]
        return rng.choices(candidates, weights=weights, k=1)[0]
    return rng.choice(candidates)


def _generate_unmatched_value(col: ColumnDef, rng: random.Random) -> Any:
    """池外值 — 模拟脏数据 / LEFT JOIN 拿不到值的场景。

    简单实现:用一个 UUID-like 字符串(几乎肯定不在池里);数值列用大负数。
    后续 P1 可改成 "类似格式但不在池" 的更智能模拟。
    """
    t = (col.type or "").upper()
    if any(k in t for k in ("INT", "BIGINT", "NUMBER", "DECIMAL", "FLOAT", "DOUBLE", "NUMERIC")):
        return -rng.randint(1_000_000, 9_999_999)
    return f"__UNMATCHED_{rng.randint(10000, 99999)}"


def _uuid_short(rng: random.Random) -> str:
    return "".join(rng.choices("abcdef0123456789", k=12))


def _zipf_int(rng: random.Random, lo: int, hi: int, alpha: float) -> int:
    n = hi - lo + 1
    weights = [1.0 / (i ** alpha) for i in range(1, n + 1)]
    idx = rng.choices(range(n), weights=weights, k=1)[0]
    return lo + idx


def _parse_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"cannot parse datetime: {s!r}")


# 切片 17：realistic 数值列支持的分布族
DISTRIBUTION_KINDS = {"lognormal", "normal", "uniform", "exponential"}


def _realistic_value(col: ColumnDef, rng: random.Random) -> Any:
    """优先级（切片 17）：
    1. `dist_params` —— 数值列按分布族采样（lognormal 长尾 / normal / uniform / exponential）
    2. `values` —— AI 填的业务样本池（切片 9），均匀抽样
    3. 按类型 fallback —— DECIMAL → 价格 / INT → 计数 / DATETIME → 90 天内 / 其它 → 短字符串
    """
    if col.dist_params:
        return _sample_distribution(col.dist_params, col.type, rng)
    if col.values:
        return rng.choice(col.values)
    t = (col.type or "").upper()
    if any(k in t for k in ("DECIMAL", "NUMERIC", "FLOAT", "DOUBLE")):
        return round(rng.uniform(10.0, 5000.0), 2)
    if any(k in t for k in ("BIGINT", "INT", "SMALLINT", "TINYINT")):
        return rng.randint(1, 10000)
    if any(k in t for k in ("DATETIME", "TIMESTAMP", "DATE")):
        return datetime(2026, 1, 1) + timedelta(days=rng.randint(0, 365))
    return "".join(rng.choices("abcdefghijklmnopqrstuvwxyz", k=8))


def _sample_distribution(params: dict, col_type: str, rng: random.Random) -> Any:
    """按 `dist_params` 采样一个数值，再按列类型取整 / 取精度。

    支持 kind：lognormal（mu/sigma）/ normal（mean+std，兼容 mu/sigma 别名）/
    uniform（min/max）/ exponential（lambda，兼容 rate 别名）。
    min/max 对非 uniform 分布起 clamp 作用（截断长尾的极端值）。
    未知 kind → ValueError（让 yml 笔误立刻暴露，跟 unknown generator 一致）。
    """
    kind = str(params.get("kind", "")).strip().lower()
    if kind == "lognormal":
        mu = _as_float(params.get("mu"), 0.0)
        sigma = _as_float(params.get("sigma"), 1.0)
        val = rng.lognormvariate(mu, max(sigma, 1e-9))
    elif kind == "normal":
        mean = _as_float(params.get("mean", params.get("mu")), 0.0)
        std = _as_float(params.get("std", params.get("sigma")), 1.0)
        val = rng.normalvariate(mean, max(std, 0.0))
    elif kind == "uniform":
        lo = _as_float(params.get("min"), 0.0)
        hi = _as_float(params.get("max"), 1.0)
        val = rng.uniform(min(lo, hi), max(lo, hi))
    elif kind == "exponential":
        lam = _as_float(params.get("lambda", params.get("rate")), 1.0)
        val = rng.expovariate(lam) if lam > 0 else 0.0
    else:
        raise ValueError(f"unknown distribution kind: {kind!r}")

    # min/max clamp（uniform 已用 min/max 当区间，不再二次 clamp）
    if kind != "uniform":
        lo_raw = params.get("min")
        hi_raw = params.get("max")
        if lo_raw is not None:
            val = max(val, _as_float(lo_raw, val))
        if hi_raw is not None:
            val = min(val, _as_float(hi_raw, val))
    return _round_for_type(val, col_type)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _round_for_type(value: float, col_type: str) -> Any:
    """按列类型把采样浮点收敛：INT 族 → int；DECIMAL(p,s) → s 位小数；
    FLOAT/DOUBLE → 4 位；其它（无类型）→ 原样浮点。"""
    t = (col_type or "").upper()
    if any(k in t for k in ("BIGINT", "INT", "SMALLINT", "TINYINT")):
        return int(round(value))
    if any(k in t for k in ("DECIMAL", "NUMERIC")):
        m = re.search(r"\(\s*\d+\s*,\s*(\d+)\s*\)", t)
        scale = int(m.group(1)) if m else 2
        return round(float(value), scale)
    if any(k in t for k in ("FLOAT", "DOUBLE", "REAL")):
        return round(float(value), 4)
    return value


def _apply_transform(transform: str | None, value: Any) -> Any:
    """目前只支持 `DATE($)`，其它原样返回。下个切片再扩。"""
    if not transform:
        return value
    t = transform.strip().upper()
    if t.startswith("DATE(") and "$" in transform:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return value.split(" ")[0].split("T")[0]
    return value


# ─── anomaly application ────────────────────────────────────────────────────


def _apply_anomaly(
    data: dict[str, TableData],
    anomaly: AnomalyDef,
    all_tables: dict[str, TableDef],
    rng: random.Random,
) -> None:
    rows = data.get(anomaly.table)
    if rows is None:
        return  # 表名不在 scenario，silently skip（loader 未做 cross-ref 校验）

    kind = anomaly.kind

    if kind == "missing_rows":
        n = _count(anomaly, len(rows))
        if not n or not rows:
            return
        for i in sorted(rng.sample(range(len(rows)), min(n, len(rows))), reverse=True):
            rows.pop(i)

    elif kind == "extra_rows":
        n = _count(anomaly, len(rows))
        if not n or not rows:
            return
        pk = _find_pk_name(anomaly.table, all_tables)
        for _ in range(n):
            base = dict(rng.choice(rows))
            if pk:
                base[pk] = _uuid_short(rng)  # 仅生成字符串 PK；非字符串 PK 待 materializer 补
            rows.append(base)

    elif kind == "value_drift":
        col = anomaly.column
        if not col:
            return
        n = _count(anomaly, len(rows))
        if not n:
            return
        perturb = _perturbation(anomaly)
        for i in rng.sample(range(len(rows)), min(n, len(rows))):
            v = rows[i].get(col)
            if isinstance(v, bool):  # bool 是 int 子类，单独排除
                continue
            if isinstance(v, (int, float)):
                rows[i][col] = type(v)(v * (1 + (rng.random() - 0.5) * 2 * perturb))

    elif kind == "null_drift":
        col = anomaly.column
        if not col:
            return
        n = _count(anomaly, len(rows))
        if not n:
            return
        for i in rng.sample(range(len(rows)), min(n, len(rows))):
            rows[i][col] = None

    elif kind == "duplicate_pk":
        n = _count(anomaly, len(rows))
        if not n or not rows:
            return
        pk = _find_pk_name(anomaly.table, all_tables)
        if not pk:
            return
        for _ in range(n):
            victim = rng.choice(rows)
            dup = dict(rng.choice(rows))
            dup[pk] = victim[pk]
            rows.append(dup)

    elif kind == "type_mismatch":
        col = anomaly.column
        if not col:
            return
        n = _count(anomaly, len(rows))
        if not n:
            return
        for i in rng.sample(range(len(rows)), min(n, len(rows))):
            v = rows[i].get(col)
            rows[i][col] = "" if v is None else str(v)


def _count(anomaly: AnomalyDef, total: int) -> int:
    """count 优先于 fraction（精确控制 > 比例控制）。"""
    if anomaly.count is not None:
        return anomaly.count
    if anomaly.fraction is not None:
        return int(total * anomaly.fraction)
    return 0


def _perturbation(anomaly: AnomalyDef) -> float:
    """从 anomaly.model_extra 取 `perturbation`，支持 `±5%` / `5%` / `0.05` / int。

    默认 0.02（±2%）。
    """
    raw = (anomaly.model_extra or {}).get("perturbation")
    if raw is None:
        return 0.02
    if isinstance(raw, (int, float)):
        return float(raw)
    s_raw = str(raw)
    s = s_raw.strip().lstrip("±").rstrip("%").strip()
    try:
        v = float(s)
        return v / 100 if "%" in s_raw else v
    except ValueError:
        return 0.02


def _find_pk_name(table_name: str, all_tables: dict[str, TableDef]) -> str | None:
    """找表的 PK 列名（考虑 derives_from + column_overrides 重命名）。"""
    table = all_tables.get(table_name)
    if not table:
        return None
    for c in table.columns:
        if c.pk:
            return c.name
    if table.derives_from and table.derives_from in all_tables:
        parent = all_tables[table.derives_from]
        renames = {ov.from_: ov.rename for ov in table.column_overrides if ov.rename}
        for c in parent.columns:
            if c.pk:
                return renames.get(c.name) or c.name
    return None


# ─── Phase 14 P0-2:streaming generation ────────────────────────────────────


def iter_table_rows_streaming(
    table: TableDef,
    all_tables: dict[str, TableDef],
    scenario: Scenario,
    *,
    batch_size: int = 1000,
    rng: random.Random | None = None,
    pool: "_FKPool | None" = None,
) -> Iterator[list[Row]]:
    """流式生成单表的行,按 batch 分批 yield。

    内存:O(batch_size × col_width) + O(anomaly_indices)。1000 行/批 + 20 列 + 50B/字段
    ≈ 1 MB 恒定,千万行规模也不爆。

    **派生表(derives_from)直接 raise** —— 派生表在 streaming 模式下走 SQL 端
    `INSERT INTO derived SELECT FROM source`,不在 Python 复制行,
    所以这个函数只负责源表。caller 检 `table.derives_from` 跳过这函数。

    anomaly 处理:
    - row-level(value_drift / null_drift / type_mismatch):预采样索引集合,
      生成阶段 inline 应用
    - missing_rows:预采样跳过索引,base loop 跳掉
    - extra_rows / duplicate_pk:base loop 跑完后追加在最后一批
    """
    if table.derives_from:
        raise ValueError(
            f"iter_table_rows_streaming: derived table {table.name!r} should "
            f"go through materializer.materialize_streaming SQL-side INSERT-SELECT"
        )
    if rng is None:
        rng = random.Random(scenario.seed) if scenario.seed else random.Random()

    total_rows = table.rows or 0
    if total_rows <= 0:
        return

    # 1. 收集本表相关 anomaly
    table_anomalies = [a for a in scenario.anomalies if a.table == table.name]

    # 2. 预采样 set-level / row-level 索引
    skip_indices: set[int] = set()              # missing_rows 跳过的索引
    value_drift_indices: dict[str, set[int]] = {}  # col → 命中索引集
    null_drift_indices: dict[str, set[int]] = {}
    type_mismatch_indices: dict[str, set[int]] = {}
    extra_count = 0
    dup_count = 0
    perturbations: dict[str, float] = {}  # col → 比例
    for a in table_anomalies:
        if a.kind == "missing_rows":
            n = _count(a, total_rows)
            if n > 0:
                skip_indices.update(
                    rng.sample(range(total_rows), min(n, total_rows))
                )
        elif a.kind == "extra_rows":
            extra_count += _count(a, total_rows)
        elif a.kind == "duplicate_pk":
            dup_count += _count(a, total_rows)
        elif a.kind == "value_drift" and a.column:
            n = _count(a, total_rows)
            if n > 0:
                value_drift_indices.setdefault(a.column, set()).update(
                    rng.sample(range(total_rows), min(n, total_rows))
                )
                perturbations[a.column] = _perturbation(a)
        elif a.kind == "null_drift" and a.column:
            n = _count(a, total_rows)
            if n > 0:
                null_drift_indices.setdefault(a.column, set()).update(
                    rng.sample(range(total_rows), min(n, total_rows))
                )
        elif a.kind == "type_mismatch" and a.column:
            n = _count(a, total_rows)
            if n > 0:
                type_mismatch_indices.setdefault(a.column, set()).update(
                    rng.sample(range(total_rows), min(n, total_rows))
                )

    pk = _find_pk_name(table.name, all_tables)

    # Phase 14 #3 Round 6 — per-table fk_unique 状态(全表去重)
    unique_seen: dict[str, set[Any]] = {
        c.name: set() for c in table.columns if c.gen == "foreign_key" and c.fk_unique
    }

    # 3. base loop:按 batch 攒行,inline 应用 row-level anomaly,跳 missing
    batch: list[Row] = []
    extra_pool: list[Row] = []  # 给 extra_rows / duplicate_pk 留几行做模板
    for i in range(total_rows):
        if i in skip_indices:
            continue
        row: Row = {c.name: _generate_value(c, rng, i, pool, unique_seen) for c in table.columns}
        # row-level anomaly inline
        for col, idx_set in value_drift_indices.items():
            if i in idx_set:
                v = row.get(col)
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    row[col] = type(v)(v * (1 + (rng.random() - 0.5) * 2 * perturbations.get(col, 0.02)))
        for col, idx_set in null_drift_indices.items():
            if i in idx_set:
                row[col] = None
        for col, idx_set in type_mismatch_indices.items():
            if i in idx_set:
                v = row.get(col)
                row[col] = "" if v is None else str(v)
        batch.append(row)
        # extra/dup 模板池(随机存几行,后面追加用)
        if (extra_count or dup_count) and len(extra_pool) < 50 and rng.random() < 0.1:
            extra_pool.append(dict(row))
        if len(batch) >= batch_size:
            yield batch
            batch = []

    # 4. extra_rows:append e 个新行(新 PK)
    if extra_count and (extra_pool or table.columns):
        for _ in range(extra_count):
            template = dict(rng.choice(extra_pool)) if extra_pool else {
                c.name: _generate_value(c, rng, total_rows + _, pool, unique_seen) for c in table.columns
            }
            if pk:
                template[pk] = _uuid_short(rng)
            batch.append(template)
            if len(batch) >= batch_size:
                yield batch
                batch = []

    # 5. duplicate_pk:append d 个重复行(沿用现 PK)
    if dup_count and extra_pool:
        for _ in range(dup_count):
            victim = rng.choice(extra_pool)
            dup = dict(rng.choice(extra_pool))
            if pk:
                dup[pk] = victim.get(pk)
            batch.append(dup)
            if len(batch) >= batch_size:
                yield batch
                batch = []

    if batch:
        yield batch


def estimate_total_rows(table: TableDef, scenario: Scenario) -> int:
    """估算 streaming 实际会产出多少行(扣掉 missing,加上 extra/dup)。

    给 caller 报 summary.rows_generated 用 —— 不实际跑 generation,纯计算。
    """
    if table.derives_from:
        return table.rows  # 派生表 SQL-side 复制,行数 = source 期望
    base = table.rows or 0
    missing = sum(_count(a, base) for a in scenario.anomalies
                  if a.table == table.name and a.kind == "missing_rows")
    extra = sum(_count(a, base) for a in scenario.anomalies
                if a.table == table.name and a.kind == "extra_rows")
    dup = sum(_count(a, base) for a in scenario.anomalies
              if a.table == table.name and a.kind == "duplicate_pk")
    return max(0, base - missing) + extra + dup
