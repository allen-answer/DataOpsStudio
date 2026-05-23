"""Phase 14: AI filler v3 — Faker locale fallback。

provider=off / API call 失败 / 没配 AI 时,ai_filler 仍能给 realistic 列生成业务
样本。比纯 random 强:走 Faker locale 拿真实姓名 / 邮箱 / 城市 / 电话等。

设计:
- column.name pattern → Faker method 的 curated mapping(name/email/city/phone 等)
- locale 推断:`scenario.domain.vertical` 含「中国」/「国内」/「电商」等 → `zh_CN`,
  否则默认 `en_US`(用户可在 ai_filler 里覆盖)
- 不匹配的列名:fallback 到类型嗅探(int → randint / str → faker.word)
- 不会失败:Faker 不在 build 时 ImportError 直接 return None,让 generator 走
  类型嗅探,跟本切片前完全一致

调用 mode:
    from app.scenarios.faker_fallback import generate_faker_values
    values = generate_faker_values("user_name", col_type="VARCHAR(100)", n=20, locale="zh_CN")
    # → ["张三", "李四", ...] 或 None(Faker 不在环境)
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# column.name pattern(小写) → Faker method 名 mapping。优先 long-prefix(精确)
# 后 short-keyword(模糊)。method 是 Faker 实例的方法名,call 取 str。
_COL_NAME_PATTERNS: list[tuple[str, str]] = [
    # 精确名(优先)
    ("user_name", "name"),
    ("customer_name", "name"),
    ("full_name", "name"),
    ("first_name", "first_name"),
    ("last_name", "last_name"),
    ("email", "email"),
    ("phone_number", "phone_number"),
    ("phone", "phone_number"),
    ("mobile", "phone_number"),
    ("address", "address"),
    ("city", "city"),
    ("province", "province"),  # zh_CN 专属;其它 locale fallback to street_address
    ("country", "country"),
    ("zipcode", "postcode"),
    ("postal_code", "postcode"),
    ("company", "company"),
    ("company_name", "company"),
    ("job_title", "job"),
    ("url", "url"),
    ("ipv4", "ipv4"),
    ("user_agent", "user_agent"),
    ("credit_card", "credit_card_number"),
    ("bank_account", "iban"),
    ("date_of_birth", "date_of_birth"),
    ("birthday", "date_of_birth"),
    # 模糊 keyword(后)
    ("name", "name"),
    ("title", "sentence"),
    ("description", "sentence"),
    ("comment", "sentence"),
    ("note", "sentence"),
    ("remark", "sentence"),
]


def detect_locale_from_scenario(scenario: Any) -> str:
    """从 scenario.domain.vertical / hint 推断 Faker locale。

    中文相关关键词 → zh_CN;其它默认 en_US。**locale 选项保守** —— Faker 支持
    100+ locale,但本系统只内置 zh_CN / en_US 两档,真要其它 locale 让用户在
    后续 enhancement 里加 scenario.ai.faker_locale 字段显式指定。
    """
    domain = getattr(scenario, "domain", None)
    if domain is None:
        return "en_US"
    text = f"{getattr(domain, 'vertical', '') or ''} {getattr(domain, 'hint', '') or ''}".lower()
    cn_kw = ("china", "chinese", "中国", "国内", "电商", "供应链",
             "金融", "保险", "银行", "证券", "支付", "物流", "零售")
    if any(kw in text for kw in cn_kw):
        return "zh_CN"
    return "en_US"


def generate_faker_values(
    column_name: str,
    *,
    col_type: str = "",
    n: int = 20,
    locale: str = "en_US",
    seed: int | None = None,
) -> list[Any] | None:
    """给 column_name + col_type 生成 n 个 Faker 业务样本。

    返回 None 表示 Faker 不在 build 里;返回 list 表示生成成功(可能值类型混合 ——
    Faker date_of_birth 返 datetime.date,phone_number 返 str)。caller 负责 coerce。

    `seed` 可选 —— 同 seed 跑同列名同 type 复现相同结果(便于测试 + scenario seed
    可复现性)。
    """
    try:
        from faker import Faker  # type: ignore
    except ImportError:
        logger.info("faker not installed; skip faker fallback")
        return None
    try:
        fake = Faker(locale)
    except Exception as exc:
        logger.warning("Faker locale=%s init failed: %s; fall to en_US", locale, exc)
        fake = Faker("en_US")
    if seed is not None:
        Faker.seed(seed)

    method = _pick_method(column_name)
    if method is None:
        # 类型嗅探兜底
        return _type_fallback(fake, col_type, n)

    out: list[Any] = []
    method_callable = getattr(fake, method, None)
    if not callable(method_callable):
        return _type_fallback(fake, col_type, n)
    for _ in range(n):
        try:
            val = method_callable()
            out.append(val)
        except Exception:
            # 单次生成失败不影响其它
            continue
    # dedup + 保序
    seen: set[Any] = set()
    deduped: list[Any] = []
    for v in out:
        try:
            if v in seen:
                continue
            seen.add(v)
        except TypeError:
            # 非 hashable(如 dict)直接 append 不去重
            pass
        deduped.append(v)
    return deduped


def _pick_method(column_name: str) -> str | None:
    name = (column_name or "").strip().lower()
    if not name:
        return None
    # 先精确(长前缀),后模糊
    for pattern, method in _COL_NAME_PATTERNS:
        if re.search(rf"\b{re.escape(pattern)}\b", name) or name == pattern:
            return method
    # 部分含(如 user_email_addr → email)
    for pattern, method in _COL_NAME_PATTERNS:
        if pattern in name:
            return method
    return None


def _type_fallback(fake: Any, col_type: str, n: int) -> list[Any]:
    """列名匹配不上时按 SQL 类型嗅探兜底。"""
    t = (col_type or "").upper()
    if any(k in t for k in ("INT", "BIGINT", "DECIMAL", "NUMERIC", "NUMBER", "FLOAT", "DOUBLE")):
        return [fake.random_int(min=1, max=10000) for _ in range(n)]
    if any(k in t for k in ("DATE", "TIME", "TIMESTAMP")):
        return [fake.date() for _ in range(n)]
    # 默认字符串
    return [fake.word() for _ in range(n)]
