# DB 语句超时

安全加固方案 P0。`resource_guard` 护应用、`sql_preflight` 拦坏 SQL —— 但都
挡不住一条**已经在跑**的慢查询长期占住数据库连接。语句超时是数据库侧的兜底：
查询跑过时限自动被服务端中止。

实现：`app/dbclients/dialects/*.py` 的 `Dialect.statement_timeout_sql()` /
`Dialect.apply_call_timeout()` + `app/dbclients/factory.py` 的
`_apply_statement_timeout()`(双路径派发)。

## 机制

每次查询执行前（`_fetch_with_dbapi` / `_iter_with_dbapi` /
`fetch_column_details`），factory 在 `cursor.execute(<业务 SQL>)` 之前
**best-effort** 下发会话级超时设置。两路径择优:

1. **连接属性路径** — 驱动暴露 round-trip 级 `conn.callTimeout`(oracledb /
   cx_Oracle / 多数 dmPython 版本)。`Dialect.apply_call_timeout(conn, sec)`
   设属性返 True,作用于该连接所有后续 round-trip。
2. **会话 SQL 路径** — `cursor.execute(<SET ...>)`,作用于该会话。MySQL 用
   `SET SESSION MAX_EXECUTION_TIME=<ms>`。

派发顺序:caller 给 connection 时优先试 (1),返 False 则 fallback (2)。
caller 没给 connection 时直接走 (2)(向后兼容老路径)。

**best-effort 语义**：下发失败只记 `warning`，**绝不让真查询陪葬**。超时是
安全网，不是查询的前置条件 —— MariaDB / 老版本 / 不支持的方言下发会报错，
吞掉即可。所以这个能力**上线即生效且零破坏风险**：支持的服务器拿到保护，
不支持的服务器行为跟以前完全一样。

## 方言覆盖矩阵

| 方言 | 路径 | 说明 |
|---|---|---|
| MySQL | SQL `SET SESSION MAX_EXECUTION_TIME=<ms>` | `max_execution_time` 毫秒,只作用于只读 SELECT —— 正好是本系统所有查询的形态 |
| Oracle | 属性 `conn.callTimeout=<ms>` | oracledb / cx_Oracle round-trip 级超时,超过即驱动抛 DPI-1067。**全 round-trip 生效**(execute / fetch 都计时) |
| DM | 属性 `conn.callTimeout=<ms>`(继承 Oracle) | dmPython 多数版本兼容 oracledb 属性接口;不兼容时 setattr AttributeError 被吞 —— 行为退化为「无超时」与上线前一致 |
| DB2 | 无 | 缺口。ibm_db / ibm_db_dbi 暂未抽象,需后续切片(`SET CURRENT QUERY OPTIMIZATION` / driver `set_option` 都可探索) |

> MariaDB 走 pymysql 但不认 `MAX_EXECUTION_TIME`（它用 `max_statement_time`）。
> 下发会失败，由 best-effort 包装吞掉 —— MariaDB 数据源行为不变。

> Oracle / DM 的属性路径需要 driver 暴露 `Connection.callTimeout`。oracledb
> 全版本支持;cx_Oracle 5.1+ 支持;dmPython 版本依赖 —— setattr AttributeError
> 被 try/except 吞掉,行为退化为「不超时」(等同于本切片前)。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS` | `900` | 语句超时秒数（15 分钟）。`<= 0` 关闭。每次查询读一遍，改 env 无需重启即生效 |

默认 **900 秒开启** —— 15 分钟对任何正常查询都足够宽松，只会掐掉真正失控的
查询。有合理的超长查询需求时调大该值；要完全关闭设 `0`。

## 未覆盖（后续切片）

- DB2 的语句超时(`ibm_db.set_option` / `ibm_db_dbi` 路径未抽象,待真实需求出现再补)。
- preview 与 compare 分别用不同超时预算(现所有查询共用一个值)。Phase 13
  `RunLimits.query_timeout_seconds` 切片会让单任务能覆盖全局默认。
- 数据库侧账号级硬限制（`max_execution_time` 全局默认、Resource Manager
  consumer group）—— 那是 DBA 侧配置，不在应用代码内。
