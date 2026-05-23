"""Scenario AI filler —— 让 `scenario.ai.fill` 配置真正发力（Phase 12 切片 9）。

把 yml 里 `gen: realistic` 的列从「随机均匀分布」升级成「LLM 给的业务化样本池」：
- ai.fill 含 `column_values` → 对每个 realistic 列，按 (table.name, col.name, type,
  domain.vertical, domain.hint) 问 LLM 要 20~30 个真实样本，写进 col.values
- ai.fill 含 `table_descriptions` → 对每张缺 description 的表，问 LLM 要 1 句
  中文摘要

generator 已经对 col.values 非空时优先采样（_realistic_value 加了 fast path），
所以 ai_filler 输出和 generator 自然衔接。

调用模式：
    filled, report = fill_scenario(scenario)
    data = generate_scenario(filled)  # 用业务化分布

provider=off / api_key 缺 / call 失败时降级：不抛，写 warning 到 report.errors，
fill 字段保持原样（实现「AI 是 nice-to-have，关掉一切照旧」的不变量）。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.scenarios.generator import DISTRIBUTION_KINDS
from app.scenarios.models import ColumnDef, Scenario, TableDef


logger = logging.getLogger(__name__)


# 单次 fill_scenario 最多发出的 LLM 调用数，防大 scenario 把 token 烧爆
_DEFAULT_MAX_CALLS = 50


VALUES_PROMPT = (
    "你是测试数据领域专家。用户在做电商 / 供应链 / 金融 等业务的对比测试，\n"
    "需要给某张表的某列生成一组「真实业务样本值」当采样池（generator 会从这\n"
    "池子里随机抽样）。\n\n"
    "硬性规则：\n"
    "- 仅返回 JSON `{\"values\": [...]}` —— 20~30 个值，类型跟 column.type 兼容\n"
    "- 不要 markdown / 不要解释；不要重复值；不要明显占位（test / foo / bar）\n"
    "- 数值类型给合理的业务量级（金额 10~5000、库存 0~10000、用户数 1~100000 等）\n"
    "- 字符串类型贴 domain.vertical + column.name 语义（user_name → 像中文姓名；\n"
    "  sku → 像 SKU 编码 SKU-A1234；status → 业务枚举值）\n"
    "- 日期 / 时间型给 ISO 字符串\n"
    "- 不要发明用户 PII（真实姓名 / 真实身份证 / 真实电话），用合理示例值即可"
)


DISTRIBUTION_PROMPT = (
    "你是测试数据领域专家。用户在做业务数据对比测试，需要给某张表的某个\n"
    "「数值列」一个真实的概率分布参数 —— 让生成的数据有真实长尾 / 集中趋势，\n"
    "而不是均匀随机。\n\n"
    "硬性规则：\n"
    "- 仅返回 JSON，不要 markdown / 不要解释\n"
    "- 形如 `{\"kind\": \"lognormal\", \"mu\": 4.5, \"sigma\": 0.7, \"min\": 1, \"max\": 50000}`\n"
    "- kind 只能是 lognormal / normal / uniform / exponential 之一：\n"
    "  · 金额 / 客单价 / 时长 这类右偏长尾 → lognormal（mu/sigma 是「对数」空间参数）\n"
    "  · 年龄 / 评分 这类对称集中 → normal（mean/std）\n"
    "  · 无明显规律 → uniform（min/max）\n"
    "  · 间隔 / 等待时间 → exponential（lambda）\n"
    "- 必须给 min / max 当业务合理边界（generator 会 clamp 掉极端值）\n"
    "- 参数要让多数样本落在 column.name 对应的合理业务量级里"
)


DESCRIPTION_PROMPT = (
    "你是数据工程师。用户给你一张表的 schema（表名 + 列 + domain 业务上下文），\n"
    "请给一句中文描述（≤30 字），说明这张表在业务里的作用。\n\n"
    "硬性规则：\n"
    "- 仅返回 JSON `{\"description\": \"...\"}`\n"
    "- 中文，不超过 30 字；动名词开头（如「订单事实表」/「商品维度表」）\n"
    "- 不要复述列名 / 不要解释每列；不要 markdown"
)


@dataclass
class FillReport:
    ok: bool = True
    calls: int = 0
    filled_columns: list[str] = field(default_factory=list)  # "table.col"
    filled_distributions: list[str] = field(default_factory=list)  # "table.col"（切片 17）
    filled_descriptions: list[str] = field(default_factory=list)  # "table"
    errors: list[str] = field(default_factory=list)
    skipped_reason: str = ""  # provider=off / 没 ai.fill 等


def fill_scenario(
    scenario: Scenario,
    *,
    max_calls: int = _DEFAULT_MAX_CALLS,
) -> tuple[Scenario, FillReport]:
    """走一遍 scenario 的 tables/columns，按 ai.fill 配置补全 values / description。

    返回 (新 scenario, 报告)。报告 ok=False 表示 provider 没启用或全部失败；
    部分成功仍 ok=True，errors 列出问题字段。
    """
    fill_scope = set(scenario.ai.fill or [])
    if not fill_scope:
        return scenario, FillReport(ok=False, skipped_reason="ai.fill is empty")

    # lazy import 避免 services → app.api 反向依赖
    from app.api.ai_utils import _call_ai
    from app.services.lineage_ai import _config as _ai_config

    config = _ai_config()
    provider_name = (config.provider or "off").lower()
    # Phase 14: provider=off 时走 Faker locale fallback —— 仍能给 column_values
    # 填业务样本,不再被 LLM 卡死。table_descriptions 跟 distribution 没 Faker
    # 等价物,这两个仍需 provider 在场。
    if provider_name in {"off", "disabled", "none", ""}:
        if "column_values" in fill_scope:
            return _fill_via_faker(scenario, fill_scope)
        return scenario, FillReport(
            ok=False,
            skipped_reason=(
                "AI provider 未启用;Faker fallback 只支持 column_values,"
                "table_descriptions / column_distributions 需 provider 在场"
            ),
        )

    # 深拷贝，避免改原 scenario
    filled = scenario.model_copy(deep=True)
    report = FillReport(ok=True, calls=0)
    all_tables = {t.name: t for t in filled.tables}

    for table in filled.tables:
        # ── column_values: 给 realistic 列填业务样本池 ───────────────────────
        if "column_values" in fill_scope:
            for col in _columns_to_fill(table, all_tables):
                if report.calls >= max_calls:
                    report.errors.append(f"hit max_calls cap ({max_calls}); stopping")
                    break
                if col.values:
                    continue  # 用户已手写 values 或上一轮 AI 填过
                report.calls += 1
                try:
                    values = _call_for_values(table, col, filled, config, provider_name, _call_ai)
                    if values:
                        col.values = values
                        report.filled_columns.append(f"{table.name}.{col.name}")
                except Exception as exc:
                    logger.warning("ai_filler values failed table=%s col=%s: %s",
                                   table.name, col.name, exc)
                    report.errors.append(f"{table.name}.{col.name}: {exc}")

        # ── column_distributions: 给 realistic 数值列填分布参数（切片 17）──────
        if "column_distributions" in fill_scope:
            for col in _columns_to_fill(table, all_tables):
                if report.calls >= max_calls:
                    report.errors.append(f"hit max_calls cap ({max_calls}); stopping")
                    break
                if col.dist_params or col.values:
                    continue  # 已有分布 / 样本池就不再问（dist_params 优先级最高）
                if not _is_numeric_type(col.type):
                    continue  # 分布参数只对数值列有意义
                report.calls += 1
                try:
                    params = _call_for_distribution(
                        table, col, filled, config, provider_name, _call_ai)
                    if params:
                        col.dist_params = params
                        report.filled_distributions.append(f"{table.name}.{col.name}")
                except Exception as exc:
                    logger.warning("ai_filler distribution failed table=%s col=%s: %s",
                                   table.name, col.name, exc)
                    report.errors.append(f"{table.name}.{col.name}: {exc}")

        # ── table_descriptions: 给缺描述的表填一句中文 ───────────────────────
        if "table_descriptions" in fill_scope and not (table.description or "").strip():
            if report.calls >= max_calls:
                report.errors.append(f"hit max_calls cap ({max_calls}); stopping")
                break
            report.calls += 1
            try:
                desc = _call_for_description(table, filled, config, provider_name, _call_ai)
                if desc:
                    table.description = desc
                    report.filled_descriptions.append(table.name)
            except Exception as exc:
                logger.warning("ai_filler description failed table=%s: %s", table.name, exc)
                report.errors.append(f"{table.name}: {exc}")

    return filled, report


# ─── Phase 14: Faker locale fallback ───────────────────────────────────────


def _fill_via_faker(
    scenario: Scenario,
    fill_scope: set[str],
) -> tuple[Scenario, FillReport]:
    """provider=off 时的 Faker fallback —— 给 realistic 列从 Faker 抽业务样本。"""
    from app.scenarios.faker_fallback import detect_locale_from_scenario, generate_faker_values

    filled = scenario.model_copy(deep=True)
    report = FillReport(ok=True, calls=0, skipped_reason="使用 Faker fallback(provider=off)")
    all_tables = {t.name: t for t in filled.tables}
    locale = detect_locale_from_scenario(filled)

    for table in filled.tables:
        for col in _columns_to_fill(table, all_tables):
            if col.values:
                continue
            values = generate_faker_values(
                col.name,
                col_type=col.type or "",
                n=25,
                locale=locale,
                # 用 (scenario.id, table, col) 推 seed,让同 scenario 复现
                seed=hash(f"{filled.id}.{table.name}.{col.name}") & 0xFFFF_FFFF,
            )
            if values is None:
                # Faker 不在 build 里,degrade to original behavior
                report.errors.append(f"{table.name}.{col.name}: faker not installed")
                continue
            if values:
                # values 可能是 datetime.date / int / str 混合 —— coerce 到 str
                col.values = [str(v) for v in values]
                report.filled_columns.append(f"{table.name}.{col.name}")
    if not report.filled_columns and not report.errors:
        report.ok = False
        report.skipped_reason = "Faker fallback 跑了但没列匹配上"
    return filled, report


# ─── helpers ────────────────────────────────────────────────────────────────


def _columns_to_fill(
    table: TableDef, all_tables: dict[str, TableDef]
) -> list[ColumnDef]:
    """返回这张表里 gen=realistic 且没 values 的列。

    派生表用 derives_from 的 columns —— 直接填到 source 那张表的 values 上
    会让派生表也继承（derives_from 拷贝时 model_copy 把 ColumnDef 复制过去）。
    """
    if not table.derives_from:
        return [c for c in table.columns if c.gen == "realistic"]
    # 派生表自己没列；走 source —— 但我们正在迭代 filled.tables，source
    # 表也会被独立处理。这里直接返回空避免重复 LLM 调用。
    return []


def _call_for_values(
    table: TableDef,
    col: ColumnDef,
    scenario: Scenario,
    config: Any,
    provider_name: str,
    _call_ai: Any,
) -> list[Any]:
    payload = {
        "table_name": table.name,
        "table_role": table.role,
        "column_name": col.name,
        "column_type": col.type,
        "column_description": col.description,
        "domain_vertical": scenario.domain.vertical,
        "domain_hint": scenario.domain.hint,
        "expected_rows": table.rows,
    }
    raw = _call_ai(provider_name, config, VALUES_PROMPT, payload)
    values = raw.get("values") if isinstance(raw, dict) else None
    if not isinstance(values, list):
        return []
    # dedup 顺序保留 + 截 30 防 LLM 长漂
    seen: set[str] = set()
    out: list[Any] = []
    for v in values:
        key = str(v)
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
        if len(out) >= 30:
            break
    return out


_NUMERIC_TYPE_HINTS = (
    "INT", "BIGINT", "SMALLINT", "TINYINT",
    "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL",
)
# LLM 分布响应里允许透传的数值参数键（其它键丢弃，避免脏字段进 dist_params）
_DIST_PARAM_KEYS = ("mu", "sigma", "mean", "std", "min", "max", "lambda", "rate")


def _is_numeric_type(col_type: str) -> bool:
    t = (col_type or "").upper()
    return any(h in t for h in _NUMERIC_TYPE_HINTS)


def _call_for_distribution(
    table: TableDef,
    col: ColumnDef,
    scenario: Scenario,
    config: Any,
    provider_name: str,
    _call_ai: Any,
) -> dict | None:
    """问 LLM 要一个分布参数 dict，校验 kind 合法 + 只保留已知数值参数键。"""
    payload = {
        "table_name": table.name,
        "table_role": table.role,
        "column_name": col.name,
        "column_type": col.type,
        "column_description": col.description,
        "domain_vertical": scenario.domain.vertical,
        "domain_hint": scenario.domain.hint,
        "expected_rows": table.rows,
    }
    raw = _call_ai(provider_name, config, DISTRIBUTION_PROMPT, payload)
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind", "")).strip().lower()
    if kind not in DISTRIBUTION_KINDS:
        return None
    params: dict[str, Any] = {"kind": kind}
    for k in _DIST_PARAM_KEYS:
        v = raw.get(k)
        if isinstance(v, bool):  # bool 是 int 子类，排除
            continue
        if isinstance(v, (int, float)):
            params[k] = v
    return params


def _call_for_description(
    table: TableDef,
    scenario: Scenario,
    config: Any,
    provider_name: str,
    _call_ai: Any,
) -> str:
    payload = {
        "table_name": table.name,
        "table_role": table.role,
        "columns": [
            {"name": c.name, "type": c.type, "description": c.description}
            for c in table.columns
        ],
        "domain_vertical": scenario.domain.vertical,
        "domain_hint": scenario.domain.hint,
    }
    raw = _call_ai(provider_name, config, DESCRIPTION_PROMPT, payload)
    desc = raw.get("description") if isinstance(raw, dict) else None
    if not isinstance(desc, str):
        return ""
    desc = desc.strip()
    # 截 60 字符防漂（30 字中文 ≈ 60 字符 utf-8 内）
    return desc[:60]
