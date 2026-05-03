"""DM 数据库 ODS schema 测试数据生成。

三张表：
- ODS.ODS_ACC_FUNDACC                  ：基金账户表（每客户多个基金账户）
- ODS.ODS_AST_NOR_ACC_FUND             ：普通基金资产表（每基金账户每日持仓快照）
- ODS.ODS_AST_NOR_HOLDER_ACC_STOCK     ：普通持有人股票资产表（按客户号 + 股东号汇总持仓）

关联键：CUST_NO（客户号），跨三张表都用同样的客户号集合。
跑法（在 dataops-studio 容器里）：
    docker exec dataops-studio python /app/scripts/seed_dm_ods.py
也可指定客户数 / 每客户基金账户数：
    docker exec dataops-studio python /app/scripts/seed_dm_ods.py --customers 50 --fundaccs-per 2
"""
from __future__ import annotations

import argparse
import datetime
import os
import random
import sys

import dmPython


DM_HOST = os.getenv("DM_HOST", "dm8-test")
DM_PORT = int(os.getenv("DM_PORT", "5236"))
DM_USER = os.getenv("DM_USER", "SYSDBA")
DM_PWD = os.getenv("DM_PWD", "SYSDBA_abc123")

SCHEMA = "ODS"

# 三张表的 DDL —— 按业务语义补字段，跟真实证券业 ODS 命名贴近
DDL = {
    "ODS_ACC_FUNDACC": """
        CREATE TABLE "ODS"."ODS_ACC_FUNDACC" (
            CUST_NO        VARCHAR(32)   NOT NULL,        -- 客户号（关联键）
            FUND_ACC       VARCHAR(32)   NOT NULL,        -- 基金账户号
            BRANCH_NO      VARCHAR(8),                    -- 营业部
            OPEN_DATE      DATE,                          -- 开户日期
            ACC_STATUS     VARCHAR(8),                    -- 账户状态：N 正常 / S 销户
            CUST_NAME      VARCHAR(64),                   -- 客户名称
            ID_KIND        VARCHAR(8),                    -- 证件类型
            ID_NO          VARCHAR(32),                   -- 证件号
            MOBILE_TEL     VARCHAR(32),                   -- 手机
            ETL_DT         DATE,                          -- ETL 加工日期
            CONSTRAINT PK_ODS_ACC_FUNDACC PRIMARY KEY (FUND_ACC)
        )
    """,
    "ODS_AST_NOR_ACC_FUND": """
        CREATE TABLE "ODS"."ODS_AST_NOR_ACC_FUND" (
            BIZ_DATE       DATE          NOT NULL,        -- 业务日期
            CUST_NO        VARCHAR(32)   NOT NULL,        -- 客户号（关联键）
            FUND_ACC       VARCHAR(32)   NOT NULL,        -- 基金账户号
            CURRENCY       VARCHAR(4),                    -- 币种 CNY / USD / HKD
            BAL            NUMERIC(20,2),                 -- 余额
            AVL_BAL        NUMERIC(20,2),                 -- 可用余额
            FROZEN_BAL     NUMERIC(20,2),                 -- 冻结金额
            FETCH_BAL      NUMERIC(20,2),                 -- 可取金额
            ASSET_VAL      NUMERIC(20,2),                 -- 资产市值
            ETL_DT         DATE,
            CONSTRAINT PK_ODS_AST_NOR_ACC_FUND PRIMARY KEY (BIZ_DATE, FUND_ACC, CURRENCY)
        )
    """,
    "ODS_AST_NOR_HOLDER_ACC_STOCK": """
        CREATE TABLE "ODS"."ODS_AST_NOR_HOLDER_ACC_STOCK" (
            BIZ_DATE       DATE          NOT NULL,        -- 业务日期
            CUST_NO        VARCHAR(32)   NOT NULL,        -- 客户号（关联键）
            HOLDER_ACC     VARCHAR(32)   NOT NULL,        -- 股东账户
            EXCH_TYPE      VARCHAR(4),                    -- 交易所：1 上交所 / 2 深交所 / K 北交所
            STOCK_CODE     VARCHAR(16)   NOT NULL,        -- 证券代码
            STOCK_NAME     VARCHAR(64),                   -- 证券名称
            HOLD_QTY       NUMERIC(20,0),                 -- 持仓数量
            AVL_QTY        NUMERIC(20,0),                 -- 可用数量
            COST_PRICE     NUMERIC(20,4),                 -- 成本价
            LAST_PRICE     NUMERIC(20,4),                 -- 最新价
            MARKET_VAL     NUMERIC(20,2),                 -- 市值
            FLOAT_PNL      NUMERIC(20,2),                 -- 浮动盈亏
            ETL_DT         DATE,
            CONSTRAINT PK_ODS_AST_NOR_HOLDER_ACC_STOCK PRIMARY KEY (BIZ_DATE, HOLDER_ACC, STOCK_CODE)
        )
    """,
}


# 演示股票池（深沪两市混合），股东账户根据 EXCH_TYPE 不同前缀
STOCK_POOL = [
    ("1", "600519", "贵州茅台"),
    ("1", "601318", "中国平安"),
    ("1", "600036", "招商银行"),
    ("1", "601988", "中国银行"),
    ("1", "600028", "中国石化"),
    ("2", "000858", "五粮液"),
    ("2", "000333", "美的集团"),
    ("2", "002594", "比亚迪"),
    ("2", "000651", "格力电器"),
    ("2", "300750", "宁德时代"),
    ("K", "430047", "诺思兰德"),
    ("K", "835174", "五新隧装"),
]
ID_KINDS = ["0", "1", "2"]  # 0 身份证 / 1 护照 / 2 港澳通行证
LAST_NAMES = ["王", "李", "张", "刘", "陈", "杨", "黄", "赵", "周", "吴", "徐", "孙", "马", "朱", "胡"]
FIRST_NAMES = ["伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "军", "洋", "勇", "艳", "杰", "娟", "涛", "明"]
BRANCHES = ["001", "002", "010", "020", "031", "045"]


def fake_name() -> str:
    return random.choice(LAST_NAMES) + random.choice(FIRST_NAMES) + random.choice(FIRST_NAMES)


def fake_id_no() -> str:
    return f"3201{random.randint(1000, 9999)}{random.randint(1970, 2005)}{random.randint(1, 12):02d}{random.randint(1, 28):02d}{random.randint(1000, 9999)}"


def fake_mobile() -> str:
    return f"1{random.choice(['3', '5', '7', '8', '9'])}{random.randint(100000000, 999999999)}"


def execute(cur, sql: str) -> None:
    try:
        cur.execute(sql)
    except dmPython.DatabaseError as exc:
        # 已存在 schema / 表 / 删了不存在的对象 等冲突 —— 由 caller 决定是否吞
        raise


def ensure_schema(cur) -> None:
    cur.execute(f"SELECT COUNT(*) FROM SYSOBJECTS WHERE TYPE$ = 'SCH' AND NAME = '{SCHEMA}'")
    exists = cur.fetchone()[0] > 0
    if not exists:
        print(f"[create] schema {SCHEMA}")
        cur.execute(f'CREATE SCHEMA "{SCHEMA}"')


def drop_table_if_exists(cur, table: str) -> None:
    cur.execute(
        f"SELECT COUNT(*) FROM SYSOBJECTS WHERE NAME = '{table}' AND SCHID = "
        f"(SELECT ID FROM SYSOBJECTS WHERE TYPE$ = 'SCH' AND NAME = '{SCHEMA}')"
    )
    if cur.fetchone()[0] > 0:
        print(f"[drop] {SCHEMA}.{table}")
        cur.execute(f'DROP TABLE "{SCHEMA}"."{table}"')


def create_tables(cur) -> None:
    for name, ddl in DDL.items():
        drop_table_if_exists(cur, name)
        print(f"[create] {SCHEMA}.{name}")
        execute(cur, ddl)


def seed_data(cur, customers: int, fundaccs_per: int, biz_date: datetime.date) -> dict[str, int]:
    """生成数据 + 批量 insert。返回各表行数。"""
    counts = {"acc": 0, "fund": 0, "stock": 0}

    # ─── 1. 客户主体 + 基金账户表 ──────────────────────────────────────────
    cust_nos: list[str] = [f"C{100000 + i:08d}" for i in range(customers)]
    fund_acc_rows: list[tuple] = []
    fund_acc_by_cust: dict[str, list[str]] = {}
    for cust_no in cust_nos:
        accs = []
        for k in range(fundaccs_per):
            fund_acc = f"F{cust_no[1:]}{k:02d}"
            accs.append(fund_acc)
            fund_acc_rows.append((
                cust_no, fund_acc,
                random.choice(BRANCHES),
                biz_date - datetime.timedelta(days=random.randint(30, 1500)),
                "N" if random.random() > 0.05 else "S",
                fake_name(),
                random.choice(ID_KINDS),
                fake_id_no(),
                fake_mobile(),
                biz_date,
            ))
        fund_acc_by_cust[cust_no] = accs

    cur.executemany(
        f'INSERT INTO "{SCHEMA}"."ODS_ACC_FUNDACC" '
        "(CUST_NO, FUND_ACC, BRANCH_NO, OPEN_DATE, ACC_STATUS, CUST_NAME, ID_KIND, ID_NO, MOBILE_TEL, ETL_DT) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        fund_acc_rows,
    )
    counts["acc"] = len(fund_acc_rows)

    # ─── 2. 基金资产快照（每基金账户一条 CNY，部分客户加一条美元） ─────────
    fund_rows: list[tuple] = []
    for cust_no, accs in fund_acc_by_cust.items():
        for fund_acc in accs:
            avl = round(random.uniform(1_000, 500_000), 2)
            frozen = round(random.uniform(0, avl * 0.1), 2)
            bal = round(avl + frozen, 2)
            asset_val = round(bal + random.uniform(-bal * 0.05, bal * 0.30), 2)
            fund_rows.append((
                biz_date, cust_no, fund_acc, "CNY",
                bal, avl, frozen, avl, asset_val, biz_date,
            ))
            # 30% 客户有美元账户
            if random.random() < 0.30:
                avl_u = round(random.uniform(100, 50_000), 2)
                fund_rows.append((
                    biz_date, cust_no, fund_acc, "USD",
                    avl_u, avl_u, 0, avl_u, avl_u, biz_date,
                ))

    cur.executemany(
        f'INSERT INTO "{SCHEMA}"."ODS_AST_NOR_ACC_FUND" '
        "(BIZ_DATE, CUST_NO, FUND_ACC, CURRENCY, BAL, AVL_BAL, FROZEN_BAL, FETCH_BAL, ASSET_VAL, ETL_DT) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        fund_rows,
    )
    counts["fund"] = len(fund_rows)

    # ─── 3. 持有人股票持仓（按 cust_no + 股东账户 + 股票） ─────────────────
    # 每客户随机持仓 1~6 只股票；股东账户由 EXCH_TYPE 派生（沪 1 / 深 2 / 北 K）
    stock_rows: list[tuple] = []
    for cust_no in cust_nos:
        stock_count = random.randint(1, 6)
        chosen = random.sample(STOCK_POOL, k=stock_count)
        for exch, code, name in chosen:
            holder_acc = f"{exch}{cust_no[1:]}"
            qty = random.choice([100, 200, 300, 500, 1000, 2000, 5000])
            avl_qty = qty if random.random() > 0.1 else int(qty * 0.5)
            cost = round(random.uniform(5, 1500), 4)
            last = round(cost * random.uniform(0.85, 1.25), 4)
            mkt = round(qty * last, 2)
            pnl = round((last - cost) * qty, 2)
            stock_rows.append((
                biz_date, cust_no, holder_acc, exch, code, name,
                qty, avl_qty, cost, last, mkt, pnl, biz_date,
            ))

    cur.executemany(
        f'INSERT INTO "{SCHEMA}"."ODS_AST_NOR_HOLDER_ACC_STOCK" '
        "(BIZ_DATE, CUST_NO, HOLDER_ACC, EXCH_TYPE, STOCK_CODE, STOCK_NAME, HOLD_QTY, AVL_QTY, "
        "COST_PRICE, LAST_PRICE, MARKET_VAL, FLOAT_PNL, ETL_DT) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        stock_rows,
    )
    counts["stock"] = len(stock_rows)

    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customers", type=int, default=20, help="客户数")
    parser.add_argument("--fundaccs-per", type=int, default=2, help="每客户基金账户数")
    parser.add_argument("--biz-date", default="", help="业务日期 YYYY-MM-DD（默认昨天）")
    args = parser.parse_args()

    biz_date = (
        datetime.date.fromisoformat(args.biz_date)
        if args.biz_date
        else datetime.date.today() - datetime.timedelta(days=1)
    )

    print(f"[connect] {DM_USER}@{DM_HOST}:{DM_PORT}")
    conn = dmPython.connect(user=DM_USER, password=DM_PWD, server=DM_HOST, port=DM_PORT)
    cur = conn.cursor()

    try:
        ensure_schema(cur)
        create_tables(cur)
        conn.commit()

        print(f"[seed] customers={args.customers} fundaccs/cust={args.fundaccs_per} biz_date={biz_date}")
        counts = seed_data(cur, args.customers, args.fundaccs_per, biz_date)
        conn.commit()

        # 校验：客户号在三表里都存在
        cur.execute(f'SELECT COUNT(DISTINCT CUST_NO) FROM "{SCHEMA}"."ODS_ACC_FUNDACC"')
        acc_custs = cur.fetchone()[0]
        cur.execute(f'SELECT COUNT(DISTINCT CUST_NO) FROM "{SCHEMA}"."ODS_AST_NOR_ACC_FUND"')
        fund_custs = cur.fetchone()[0]
        cur.execute(f'SELECT COUNT(DISTINCT CUST_NO) FROM "{SCHEMA}"."ODS_AST_NOR_HOLDER_ACC_STOCK"')
        stock_custs = cur.fetchone()[0]

        print(
            f"[done] ODS_ACC_FUNDACC={counts['acc']} 行 / {acc_custs} 客户; "
            f"ODS_AST_NOR_ACC_FUND={counts['fund']} 行 / {fund_custs} 客户; "
            f"ODS_AST_NOR_HOLDER_ACC_STOCK={counts['stock']} 行 / {stock_custs} 客户"
        )
        return 0
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    random.seed(20260504)
    sys.exit(main())
