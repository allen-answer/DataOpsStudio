"""Scenario generator —— 按 Scenario 在内存中产数据（Phase 12 切片 2）。

`generate_scenario(scenario)` 返回 `{table_name: list[row_dict]}`：
- 源表用 columns 定义的 generator 现场造
- 派生表（derives_from）拷贝源数据 + 应用 column_overrides
- anomalies 在所有表造完之后 apply（破坏目标表，制造 only_source / diff / null）

seed != 0 时复跑同结果。seed=0 时不固定 RNG。

下一切片：materializer 把 dict 落到 demo MySQL；ai_filler 接管 ai.fill 字段。
"""
from __future__ import annotations

import random
import re
from datetime import date, datetime, timedelta
from typing import Any

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

    顺序：源表 → 派生表 → anomalies。同 seed 同输出。
    """
    rng = random.Random(scenario.seed) if scenario.seed else random.Random()
    all_tables = {t.name: t for t in scenario.tables}
    out: dict[str, TableData] = {}
    for table in _resolve_order(scenario.tables):
        out[table.name] = _generate_table(table, all_tables, out, rng)
    for anomaly in scenario.anomalies:
        _apply_anomaly(out, anomaly, all_tables, rng)
    return out


# ─── table-level ────────────────────────────────────────────────────────────


def _resolve_order(tables: list[TableDef]) -> list[TableDef]:
    by_name = {t.name: t for t in tables}
    seen: set[str] = set()
    order: list[TableDef] = []

    def visit(t: TableDef) -> None:
        if t.name in seen:
            return
        if t.derives_from and t.derives_from in by_name:
            visit(by_name[t.derives_from])
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
) -> TableData:
    if table.derives_from and table.derives_from in generated:
        return _derive_from(table, generated[table.derives_from])
    return [
        {c.name: _generate_value(c, rng, i) for c in table.columns}
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


def _generate_value(col: ColumnDef, rng: random.Random, row_index: int) -> Any:
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
    raise ValueError(f"unknown generator: {g}")


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
