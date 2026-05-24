"""Scenario template DSL — Pydantic models.

DSL 三层：tables（schema 结构）/ anomalies（故意制造的偏差）/ workloads
（数据被谁消费 —— compare / lineage / slow-sql / workflow）。

设计原则（详见 CLAUDE.md Phase 12）：
- **结构 deterministic / 内容 AI fill** —— template 控 schema shape，LLM
  只在 `ai.fill` 白名单字段里填业务血肉
- **三层独立扩展** —— 加新 anomaly kind / workload kind 只动 Literal 闭集
  + 注册一个 generator 函数，不动 Scenario 模型
- **预期产出落 template** —— workload 自带 `expected:` ground truth，回归
  测试 / AI 评分能拿来对比
- **`extra='forbid'` 拦未知字段** —— 让 YAML 笔误立刻报错，不蒙混过关。
  anomaly / workload 子项例外（用 `extra='allow'`），因为 kind-specific
  字段太多，列在 Literal 上反而失控
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Dialect = Literal["mysql", "oracle", "dm"]
Role = Literal["source", "target", "dim", "staging"]
Generator = Literal[
    "uuid_short",   # 短 UUID，PK 用
    "random_int",   # 含 range / distribution
    "realistic",    # AI 填分布参数 → Faker 落数据
    "timestamp",    # range = [start, end] ISO 日期
    "enum",         # values + 可选 distribution（权重列表）
    "constant",     # 固定值，column.range / values 写常量
    "sequence",     # 自增（int / 字符串前缀）
    "foreign_key",  # Phase 14 #3 Round 6:从另一表的列值池抽样,保证 JOIN 匹配
]
Distribution = Literal["uniform", "zipf", "normal", "skewed"]
FKDistribution = Literal["uniform", "zipf"]   # foreign_key 抽样分布
AnomalyKind = Literal[
    "missing_rows",   # target 缺一部分 source 的行
    "extra_rows",     # target 多出 source 没有的行
    "value_drift",    # 数值列偏移（perturbation 控幅度）
    "type_mismatch",  # 字段类型不匹配（如 DATETIME 存为 VARCHAR）
    "null_drift",     # 一部分行变 NULL
    "duplicate_pk",   # PK 重复
]
WorkloadKind = Literal[
    "compare_task",
    "lineage_script",
    "slow_query",
    "workflow_run",
]
FillScope = Literal["column_values", "table_descriptions", "column_distributions"]


class ColumnDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    pk: bool = False
    nullable: bool = True
    gen: Generator
    # gen=random_int / timestamp 用 range；gen=enum 用 values。两个互斥不强校验，
    # 由 generator 实现自己挑（保留模型简洁）
    range: list = Field(default_factory=list)
    values: list = Field(default_factory=list)
    # 单字段权重列表（如 enum 各值频率），或全局 distribution 名
    distribution: str | list[float] | None = None
    zipf_alpha: float | None = None
    # 切片 17：gen=realistic 的数值列分布参数（AI filler v2 / 手写均可）。
    # `{kind: lognormal|normal|uniform|exponential, ...params, min?, max?}`。
    # 优先级高于 values 样本池 —— 让金额 / 计数等列有真实长尾分布而非均匀抽样。
    dist_params: dict | None = None
    description: str = ""

    # ─── Phase 14 #3 Round 6:外键引用(referential integrity)─────────────
    # gen=foreign_key 用,从另一张表的列值池抽样,保证多表 JOIN 拿到匹配行。
    # 解决之前每表独立 random 生成 → JOIN 拿不到数据 → plan 跟生产偏离的问题。
    # 详见 docs/SCENARIO_LAB.md FK 章节。
    references: str | None = None  # 格式 "table.column",如 "ods.ods_acc_fundacc.fund_acc_no"
    match_rate: float = Field(1.0, ge=0.0, le=1.0)   # 1.0 = 100% 匹配;<1 模拟脏数据
    fk_unique: bool = False                            # 1:1 关系(余额表/etc)需要 True
    fk_distribution: FKDistribution = "uniform"
    fk_zipf_alpha: float = Field(1.2, ge=1.0, le=5.0)

    # Round 6 N — 金融行业 domain generator(优先级高于 dist_params / values)
    # 取值见 app/scenarios/faker_providers.py:list_providers() 注册的 13 个
    faker_provider: str = ""


class IndexDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(..., min_length=1)
    unique: bool = False
    # 故意不建（slow-sql demo 用：让 EXPLAIN 显示 full scan）
    skip: bool = False
    reason: str = ""


class ColumnOverride(BaseModel):
    """`derives_from` 表的列改写：rename / transform（`$` 占位源列）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(..., alias="from", min_length=1)
    rename: str | None = None
    transform: str | None = None  # e.g. "DATE($)"，$ = source column ref


class TableDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)  # `schema.basename` 或裸表名
    role: Role
    # Phase 14 P0-2 streaming generator 不爆内存,cap 提到 1 亿(yml_importer
    # 单表 cap 是 1_000_000,但直接写 yml 的高级用户可以上千万 / 亿级)
    rows: int = Field(default=1000, ge=0, le=100_000_000)
    columns: list[ColumnDef] = Field(default_factory=list)
    indexes: list[IndexDef] = Field(default_factory=list)
    # 引用另一张表名 —— 继承其 columns，下面 overrides 只写差异
    derives_from: str | None = None
    column_overrides: list[ColumnOverride] = Field(default_factory=list)
    description: str = ""


class AnomalyDef(BaseModel):
    """故意制造的偏差。kind-specific 字段允许透传（fraction / perturbation /
    store_as / count / ...），不在模型里枚举完。"""

    model_config = ConfigDict(extra="allow")

    kind: AnomalyKind
    table: str = Field(..., min_length=1)
    column: str | None = None
    fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    count: int | None = Field(default=None, ge=0)


class WorkloadDef(BaseModel):
    """数据用途。compare_task 带 source/target/keys/expected；slow_query 带
    sql + intentional_issues + expected_optimizations；lineage_script 带 sql。
    kind-specific 字段透传。"""

    model_config = ConfigDict(extra="allow")

    kind: WorkloadKind
    name: str = ""


class DomainDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vertical: str = "generic"  # ecommerce / supply_chain / finance / ...
    hint: str = ""  # 自由文本，喂给 LLM 当 system context


class AISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # `${default}` —— 引用 lineage_ai.json 里的默认 provider
    provider: str = "${default}"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    fill: list[FillScope] = Field(default_factory=list)


class Scenario(BaseModel):
    """顶层 scenario。一份 YAML 一个 scenario，未来支持 `extends:` 时这里加字段。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(..., min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    dialect: Dialect = "mysql"
    # 0 = 不固定 seed（每次重新生成随机）；非 0 = 复跑同结果
    seed: int = 0
    ai: AISettings = Field(default_factory=AISettings)
    domain: DomainDef = Field(default_factory=DomainDef)
    tables: list[TableDef]
    anomalies: list[AnomalyDef] = Field(default_factory=list)
    workloads: list[WorkloadDef] = Field(default_factory=list)
    # 切片 15：模板变量。workload.sql 里 `{{name}}` 占位符 → variables[name] 替换。
    # 仅支持标量值（str/int/float/bool），数据类型保持原样写入 SQL。
    variables: dict[str, str | int | float | bool] = Field(default_factory=dict)

    # ─── Phase 14 #3 Round 6 FK validator ────────────────────────────────
    @model_validator(mode="after")
    def _check_fk_references(self) -> "Scenario":
        """校验所有 gen=foreign_key 列的 references 字段合法 + 无循环。

        失败情形:
        1. gen=foreign_key 但 references 为空
        2. references 格式不对(不是 "table.column" 或 "schema.table.column")
        3. references 指向不存在的表 / 列
        4. references 自引用(gen 列 == 引用列 + 同表)— 暂不支持
        5. FK 依赖图含循环(A → B → A)
        """
        table_index: dict[str, TableDef] = {t.name: t for t in self.tables}
        # 也允许只写裸表名引用 schema.table 这种形式
        for full_name, t in list(table_index.items()):
            if "." in full_name:
                base = full_name.rsplit(".", 1)[1]
                # 不覆盖既存的同名表
                table_index.setdefault(base, t)

        # 1) per-column 校验 + 收集 FK 边
        edges: list[tuple[str, str]] = []   # (from_table, to_table)
        for table in self.tables:
            for col in table.columns:
                if col.gen != "foreign_key":
                    # 反向校验:非 fk 列不该带 references 字段
                    if col.references:
                        raise ValueError(
                            f"列 {table.name}.{col.name}: references 仅 gen=foreign_key 时有效"
                        )
                    continue
                # gen=foreign_key
                if not col.references:
                    raise ValueError(
                        f"列 {table.name}.{col.name}: gen=foreign_key 必须提供 references"
                    )
                ref = col.references.strip()
                # 支持 "table.col" 或 "schema.table.col" — 最后一个 . 拆 col,其余拼 table
                if "." not in ref:
                    raise ValueError(
                        f"列 {table.name}.{col.name} references={ref!r} 格式不对,"
                        f"应为 'table.column' 或 'schema.table.column'"
                    )
                ref_table, ref_col = ref.rsplit(".", 1)
                if ref_table not in table_index:
                    raise ValueError(
                        f"列 {table.name}.{col.name} references={ref!r} 引用的表 "
                        f"'{ref_table}' 不在 scenario.tables 内"
                    )
                ref_table_def = table_index[ref_table]
                ref_col_names = {c.name for c in ref_table_def.columns}
                if ref_col not in ref_col_names:
                    raise ValueError(
                        f"列 {table.name}.{col.name} references={ref!r} 引用的列 "
                        f"'{ref_col}' 不在表 '{ref_table}' 内 "
                        f"(该表的列: {sorted(ref_col_names)})"
                    )
                # 自引用本轮不支持
                if ref_table_def.name == table.name:
                    raise ValueError(
                        f"列 {table.name}.{col.name}: 自引用 FK 暂不支持 "
                        "(同表 references 跟生成顺序冲突)"
                    )
                edges.append((table.name, ref_table_def.name))

        # 2) 拓扑排序检测循环 — Kahn 算法
        if edges:
            from collections import defaultdict, deque
            in_degree: dict[str, int] = defaultdict(int)
            adj: dict[str, list[str]] = defaultdict(list)
            # 所有表都在图里(即使没出边),才能正确算 in_degree
            for t in self.tables:
                in_degree.setdefault(t.name, 0)
            for src, dst in edges:
                # edges 是 "table 依赖 ref_table" → 拓扑序里 ref_table 先,table 后
                # 所以箭头 dst → src(被引用的先生成)
                adj[dst].append(src)
                in_degree[src] += 1
            queue = deque(t.name for t in self.tables if in_degree[t.name] == 0)
            visited = 0
            while queue:
                cur = queue.popleft()
                visited += 1
                for nxt in adj[cur]:
                    in_degree[nxt] -= 1
                    if in_degree[nxt] == 0:
                        queue.append(nxt)
            if visited < len(self.tables):
                remaining = [n for n, d in in_degree.items() if d > 0]
                raise ValueError(
                    f"FK 引用图包含循环:涉及 {remaining}。"
                    "scenario 要求 DAG — 改写为单向引用(如 child → parent)"
                )
        return self
