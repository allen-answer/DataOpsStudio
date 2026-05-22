# DB 语句超时

安全加固方案 P0。`resource_guard` 护应用、`sql_preflight` 拦坏 SQL —— 但都
挡不住一条**已经在跑**的慢查询长期占住数据库连接。语句超时是数据库侧的兜底：
查询跑过时限自动被服务端中止。

实现：`app/dbclients/dialects/*.py` 的 `Dialect.statement_timeout_sql()` +
`app/dbclients/factory.py` 的 `_apply_statement_timeout()`。

## 机制

每次查询执行前（`_fetch_with_dbapi` / `_iter_with_dbapi` /
`fetch_column_details`），factory 在 `cursor.execute(<业务 SQL>)` 之前
**best-effort** 下发一条会话级超时设置。

**best-effort 语义**：下发失败只记 `warning`，**绝不让真查询陪葬**。超时是
安全网，不是查询的前置条件 —— MariaDB / 老版本 / 不支持的方言下发会报错，
吞掉即可。所以这个能力**上线即生效且零破坏风险**：支持的服务器拿到保护，
不支持的服务器行为跟以前完全一样。

## 方言覆盖矩阵

| 方言 | `statement_timeout_sql` | 说明 |
|---|---|---|
| MySQL | `SET SESSION MAX_EXECUTION_TIME=<ms>` | `max_execution_time` 单位毫秒，只作用于只读 SELECT —— 正好是本系统所有查询的形态 |
| Oracle | `None` | 语句超时需驱动 call timeout / Resource Manager，不是一条 SQL —— 缺口 |
| DM | `None` | 同 Oracle —— 缺口 |
| DB2 | `None` | 缺口 |

> MariaDB 走 pymysql 但不认 `MAX_EXECUTION_TIME`（它用 `max_statement_time`）。
> 下发会失败，由 best-effort 包装吞掉 —— MariaDB 数据源行为不变。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS` | `900` | 语句超时秒数（15 分钟）。`<= 0` 关闭。每次查询读一遍，改 env 无需重启即生效 |

默认 **900 秒开启** —— 15 分钟对任何正常查询都足够宽松，只会掐掉真正失控的
查询。有合理的超长查询需求时调大该值；要完全关闭设 `0`。

## 未覆盖（后续切片）

- Oracle / DM 的语句超时（驱动 call timeout / Resource Manager）。
- preview 与 compare 分别用不同超时预算（现在所有查询共用一个值）。
- 数据库侧账号级硬限制（`max_execution_time` 全局默认、Resource Manager
  consumer group）—— 那是 DBA 侧配置，不在应用代码内。
