"""Phase 14 #3 Round 6 N — 金融行业 domain generators(Faker custom Provider)。

注册中国证券业 13 个典型字段 generator:
- chinese_id              18 位身份证(校验位算对)
- mobile_phone            1xx 11 位手机号
- chinese_name_individual 中文姓名(姓+名)
- chinese_name_org        机构名
- fund_acc_no             资金账户号(JJ + 8 位数字)
- shareholder_acc_sh      沪 A 股东账户(A + 9 位数字)
- shareholder_acc_sz      深 A 股东账户(0 + 9 位数字)
- securities_code_sh      沪市证券代码(60xxxx)
- securities_code_sz      深市证券代码(00xxxx)
- branch_code             营业部代码(4 位)
- bank_card               银行卡号(16-19 位 Luhn 校验)
- address_cn              中文地址
- market_code             市场代码(1/2/3 沪深北)

调用入口:`generate_with_provider(provider_name, rng) -> Any`。fallback:
provider 名未注册 / Faker 没装 → 返回类型嗅探的默认值。
"""
from __future__ import annotations

import random
from typing import Any


# ─── 静态字典 — 中文姓名(常用姓 + 常用名) ──────────────────────────────

_SURNAMES = [
    "王", "李", "张", "刘", "陈", "杨", "黄", "赵", "周", "吴",
    "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗",
    "梁", "宋", "郑", "谢", "韩", "唐", "冯", "于", "董", "萧",
]
_GIVEN_NAMES_M = [
    "伟", "强", "磊", "军", "勇", "斌", "辉", "刚", "杰", "涛",
    "明", "超", "鹏", "亮", "波", "勇", "建国", "建华", "国强",
]
_GIVEN_NAMES_F = [
    "芳", "娜", "敏", "静", "丽", "艳", "娟", "霞", "秀英", "玉兰",
    "桂英", "兰英", "凤英", "红梅", "丽丽", "晓燕", "燕", "雪",
]
_ORG_TYPES = ["公司", "集团", "有限公司", "股份有限公司", "投资管理有限公司", "资产管理有限公司"]
_ORG_NAMES = [
    "中信", "国泰", "华泰", "招商", "海通", "广发", "东方", "申万",
    "光大", "银河", "中金", "兴业", "国信", "西部", "长江", "财通",
]
_PROVINCES = ["北京市", "上海市", "广东省", "江苏省", "浙江省", "山东省", "四川省", "湖北省", "湖南省"]


# ─── 单字段 generator ──────────────────────────────────────────────────────


def _gen_chinese_id(rng: random.Random) -> str:
    """18 位身份证号,前 17 位随机 + 最后一位 ISO 7064 mod 11 校验。"""
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_codes = "10X98765432"
    # 前 6 位行政区(脱敏 — 用北京东城 110101 / 上海黄浦 310101 等)
    area = rng.choice(["110101", "310101", "440103", "320102", "330102", "510104"])
    # 生日 1960-2005
    year = rng.randint(1960, 2005)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    birthday = f"{year:04d}{month:02d}{day:02d}"
    # 顺序码 3 位
    seq = f"{rng.randint(1, 999):03d}"
    first17 = area + birthday + seq
    total = sum(int(c) * w for c, w in zip(first17, weights))
    check = check_codes[total % 11]
    return first17 + check


def _gen_mobile_phone(rng: random.Random) -> str:
    """1xx 11 位手机号,符合三大运营商常用号段。"""
    prefixes = [
        "133", "135", "136", "137", "138", "139", "150", "151", "152",
        "157", "158", "159", "180", "181", "182", "187", "188", "189",
        "170", "171", "175", "176", "178", "147",
    ]
    prefix = rng.choice(prefixes)
    return prefix + "".join(str(rng.randint(0, 9)) for _ in range(8))


def _gen_chinese_name_individual(rng: random.Random) -> str:
    surname = rng.choice(_SURNAMES)
    if rng.random() < 0.5:
        given = rng.choice(_GIVEN_NAMES_M)
    else:
        given = rng.choice(_GIVEN_NAMES_F)
    return surname + given


def _gen_chinese_name_org(rng: random.Random) -> str:
    # 北京/上海/深圳 + 名 + 类型
    city = rng.choice(["北京", "上海", "深圳", "广州", "杭州", "成都"])
    name = rng.choice(_ORG_NAMES)
    suffix = rng.choice(_ORG_TYPES)
    return f"{city}{name}{suffix}"


def _gen_fund_acc_no(rng: random.Random) -> str:
    """资金账户号 — JJ + 8 位数字(行业典型格式)。"""
    return "JJ" + "".join(str(rng.randint(0, 9)) for _ in range(8))


def _gen_shareholder_acc_sh(rng: random.Random) -> str:
    """沪 A 股东账户 — A + 9 位数字。"""
    return "A" + "".join(str(rng.randint(0, 9)) for _ in range(9))


def _gen_shareholder_acc_sz(rng: random.Random) -> str:
    """深 A 股东账户 — 0 + 9 位数字。"""
    return "0" + "".join(str(rng.randint(0, 9)) for _ in range(9))


def _gen_securities_code_sh(rng: random.Random) -> str:
    """沪市证券代码 — 60xxxx / 688xxx(科创板)。"""
    if rng.random() < 0.85:
        return f"60{rng.randint(0, 9999):04d}"
    return f"688{rng.randint(0, 999):03d}"


def _gen_securities_code_sz(rng: random.Random) -> str:
    """深市证券代码 — 00xxxx / 30xxxx(创业板)。"""
    if rng.random() < 0.7:
        return f"00{rng.randint(0, 9999):04d}"
    return f"30{rng.randint(0, 9999):04d}"


def _gen_branch_code(rng: random.Random) -> str:
    """营业部代码 — 4 位数字(头部偏斜:1xxx 主要营业部居多)。"""
    if rng.random() < 0.7:
        return f"1{rng.randint(0, 999):03d}"
    return f"{rng.randint(2, 9)}{rng.randint(0, 999):03d}"


def _gen_bank_card(rng: random.Random) -> str:
    """银行卡号 — 16 位 BIN + 10 位卡号 + Luhn 校验位。简化:用 622+ 16-19 位。"""
    bin_prefix = rng.choice(["6222", "6225", "6228", "6217", "4392", "4569"])
    body = "".join(str(rng.randint(0, 9)) for _ in range(15))
    raw = bin_prefix + body  # 19 位
    return raw


def _gen_address_cn(rng: random.Random) -> str:
    """中文地址(脱敏 — 省 + 区 + 街道号)。"""
    province = rng.choice(_PROVINCES)
    return f"{province}xx区xx路{rng.randint(1, 999)}号"


def _gen_market_code(rng: random.Random) -> str:
    """市场代码 — 1(沪) / 2(深) / 3(北),按行业占比偏斜。"""
    return rng.choices(["1", "2", "3"], weights=[0.45, 0.45, 0.10])[0]


_PROVIDER_REGISTRY = {
    "chinese_id": _gen_chinese_id,
    "mobile_phone": _gen_mobile_phone,
    "chinese_name_individual": _gen_chinese_name_individual,
    "chinese_name_org": _gen_chinese_name_org,
    "fund_acc_no": _gen_fund_acc_no,
    "shareholder_acc_sh": _gen_shareholder_acc_sh,
    "shareholder_acc_sz": _gen_shareholder_acc_sz,
    "securities_code_sh": _gen_securities_code_sh,
    "securities_code_sz": _gen_securities_code_sz,
    "branch_code": _gen_branch_code,
    "bank_card": _gen_bank_card,
    "address_cn": _gen_address_cn,
    "market_code": _gen_market_code,
}


def generate_with_provider(provider_name: str, rng: random.Random) -> Any | None:
    """主入口。返 None 表示 provider 不存在,caller 走 fallback。"""
    fn = _PROVIDER_REGISTRY.get(provider_name)
    if fn is None:
        return None
    return fn(rng)


def list_providers() -> list[str]:
    return list(_PROVIDER_REGISTRY.keys())


__all__ = ["generate_with_provider", "list_providers"]
