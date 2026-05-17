# 数据库驱动矩阵 / 离线部署风险

按数据库列出 Python 驱动选择、安装姿势、对 sqlglot 方言映射、以及客户离线环境最容易踩的坑。后台运行时通过 `app/dbclients/drivers.py` 的 `DRIVER_MODULES` 表懒加载，缺驱动报 `RuntimeError: <db> driver is not installed`。

---

## 总览

| DataBase | 候选驱动（按优先级） | requirements.txt 默认 | 是否需系统依赖 | sqlglot 方言 | 内部 Dialect 类 |
|----------|---------------------|---------------------|--------------|-------------|----------------|
| **MySQL** | `pymysql` → `MySQLdb` | ✅ `pymysql` + `cryptography` | 否（纯 Python） | `mysql` | `MysqlDialect` |
| **DM 达梦** | `dmPython` | ✅ `dmPython` | 是（DM 客户端 / so） | `oracle`（DM 兼容 Oracle） | `DmDialect`（继承 Oracle） |
| **Oracle** | `oracledb` → `cx_Oracle` | ❌（注释掉） | thin 模式无需，thick 需 instantclient | `oracle` | `OracleDialect` |
| **DB2** | `ibm_db_dbi` / `ibm_db` | ❌（注释掉） | **是（必须装 IBM CLI driver / clidriver）** | `db2` | `Db2Dialect` |
| **OceanBase（MySQL 模式）** | 复用 `pymysql` | ✅（同 MySQL） | 否 | `mysql` / `ob_mysql` | 复用 `MysqlDialect` |
| **OceanBase（Oracle 模式）** | 复用 `oracledb` 或 `cx_Oracle` | ❌ | 同 Oracle | `oracle` / `ob_oracle` | 复用 `OracleDialect` |

> ⚠️ 入库 / 连接走「DB 类型 → driver 模块」（`DRIVER_MODULES`），OceanBase 当前没有独立 `DatabaseType` 枚举值，按底层兼容协议复用 MySQL / Oracle 这条链。如客户场景需要把 OB 单独区分，需要：(1) `app/models/common.py` 加 `OB_MYSQL` / `OB_ORACLE` 两个 enum；(2) `DRIVER_MODULES` 复用同一组模块；(3) `app/dbclients/dialects/__init__.py` 注册同样的 Dialect 实例。

---

## MySQL

### 驱动
- **首选 `pymysql`**：纯 Python，最容易在离线机器上装（一个 wheel 就行）。
- `cryptography`：MySQL 8 默认走 `caching_sha2_password`，pymysql 必须配 cryptography 才能登录。装 pymysql 必须连带装。
- 备选 `MySQLdb`（`mysqlclient`）：需要 `libmysqlclient-dev` / VC++ 编译，速度稍快但部署成本高，不推荐。

### 安装
```bash
pip install pymysql cryptography
```

### 离线打包
`scripts/build_offline_windows.ps1` 会把 `requirements.txt` 声明的 `pymysql + cryptography` 一起 download 进 wheels 目录，离线机器 `install.bat` 一键装。**无系统依赖**。

### 已知风险 / 坑
- MySQL 8 + pymysql 5.x：必须带 cryptography，否则报 `RuntimeError: 'cryptography' package is required`。
- 字符集：默认 `utf8mb4`；老库 `utf8`（其实是 `utf8mb3`）时 emoji / 4 字节字符会乱码。
- 时区：服务器 timezone 表没初始化时 `CONVERT_TZ` 返 NULL，对比结果可能出 diff（建议两边显式 SET）。
- `cursor.fetchmany` 在某些 mysql 配置下默认 100 行一拉，大表注意 `RunLimits.fetch_chunk_size` 调到 5000+。

---

## DM 达梦

### 驱动
- **唯一选项 `dmPython`** —— DM 官方提供，从达梦官网下载（pip 上有 mirror 但版本滞后，强烈建议用厂商带过来的版本）。
- DM 跟 Oracle 在 DDL / PL/SQL / 数据字典视图（`ALL_TAB_COLUMNS` / `USER_*`）高度兼容，所以 sqlglot 方言、`OracleDialect` 全部复用，DM 只在 `connect()` 时单独走 dmPython 的入参（`server` / `port` / `user` / `password` / `schema` option）。

### 安装
```bash
# 厂商 wheel
pip install dmPython-X.Y.Z-cpXY-cpXY-win_amd64.whl
# 或 Linux
pip install dmPython-X.Y.Z-cpXY-cpXY-linux_x86_64.whl
```

dmPython 是 C 扩展，**有 ABI 锁**：必须跟目标机器 Python 主次版本一致（`cp312` 不能装到 `cp311`），且 OS / 架构匹配。

### 离线打包
1. 从达梦官网拿到目标机器对应的 wheel（Python 版本 + OS + arch 三件套）；
2. 放进 `wheels/` 目录里；
3. `install.bat` / `pip install --no-index --find-links wheels dmPython`。

### 已知风险 / 坑
- **wheel ABI 错配**是最常踩的雷：客户机 Python 3.10 + 你打包用了 3.12 的 wheel → `ImportError`。打包前必须问清楚。
- DM 没有 MySQL 那种 `information_schema.COLUMNS`，introspect 走 `ALL_TAB_COLUMNS + ALL_COL_COMMENTS`（已在 `services/datasource_introspect.py` 走 Oracle path）。
- `schema` option：dmPython 接 `schema=` 关键字时部分老版本会拒绝，`DmDialect.connect()` 已加 fallback 走 positional（详见 `app/dbclients/dialects/dm.py`）。
- DM 大小写：默认大写不敏感但保留原样，慎用混合大小写表名。
- 错误码：dmPython 抛 `dmPython.Error`，`_extract_driver_error_detail` 走 `cursor.errno` / `cursor.errmsg` best-effort 探测。

---

## Oracle

### 驱动
- **首选 `oracledb`**（python-oracledb，Oracle 官方维护的 cx_Oracle 继任者）—— **thin 模式默认无系统依赖**，纯 Python wire 协议实现。
- 备选 `cx_Oracle`：thick 模式必须 instantclient + LD_LIBRARY_PATH / PATH。

### 安装
```bash
pip install oracledb
# 默认 thin 模式，不需要 instantclient
```

如要 thick 模式（用 OCI 客户端能力，如 LDAP 名字解析、Kerberos）：
```bash
pip install oracledb
# 同时下载 Oracle Instant Client basic 包，解压后:
export ORACLE_HOME=/opt/oracle/instantclient_21_X
export LD_LIBRARY_PATH=$ORACLE_HOME:$LD_LIBRARY_PATH  # Linux
# Windows 把 instantclient 目录加 PATH
```

### 离线打包
- thin 模式：oracledb wheel 直接 download 即可，**无 instantclient 依赖**，零额外文件。
- thick 模式：要带 ~80MB 的 instantclient zip，部署时解压 + 配 PATH。

### 已知风险 / 坑
- Oracle 12c 之前的老库不支持 thin 模式，需要 thick + instantclient。
- DSN 格式：`host:port/service_name` 是 service name；`host:port:sid` 是 SID，两种不能混。`OracleDialect.connect()` 支持 `extra.dsn` 直接传完整字符串覆盖。
- `EXPLAIN PLAN`：Oracle 不是单行 EXPLAIN，要 `EXPLAIN PLAN SET STATEMENT_ID FOR ...` 再 `SELECT FROM PLAN_TABLE`。`services/slow_sql.py` 已按方言派发。
- 大小写：Oracle 默认存储为大写，识别符走 `"name"` 才保大小写。
- 字符集 / NLS：`NLS_LANG=AMERICAN_AMERICA.AL32UTF8` 否则中文乱码。

---

## DB2

### 驱动
- 唯一选项 `ibm_db` / `ibm_db_dbi`（IBM 官方），**强依赖 IBM CLI driver（clidriver）**。

### 安装
```bash
pip install ibm_db
# 会自动下载 ~30MB 的 clidriver 到 site-packages，但客户离线网络拿不到
```

### 离线打包（**最难的一个**）
1. 在有网机器上 `pip install ibm_db` —— 它会下载 clidriver 解压到 `<site-packages>/clidriver/`；
2. 把整个 `clidriver/` 目录跟 wheel 一起打包；
3. 客户机解压后，**Windows** 设 `IBM_DB_HOME=<解压路径>/clidriver`（`app/dbclients/drivers.add_db2_dll_directories()` 会自动把 `bin/`、`bin/amd64.VC12.CRT` / `VC14.CRT` 加到 PATH）；**Linux** 设 `IBM_DB_DIR` + `LD_LIBRARY_PATH`。
4. Windows 还要装 VC++ 2013 + 2015 redistributable（VC12 / VC14 CRT 依赖）。

### 已知风险 / 坑
- **clidriver 自动下载在离线环境必失败**，必须手工带过去。这是离线部署 DB2 失败率最高的一类。
- VC++ 运行库缺失：报 `ImportError: DLL load failed` 时 99% 是 VC redistributable 没装。
- `ibm_db` 的错误叫 cursor 「returned a result with an exception set」 —— Python 通用层把真错（SQLCODE/SQLSTATE）吞了，`_extract_driver_error_detail` 走 `ibm_db.stmt_errormsg()` / `conn_errormsg()` 反查。报错时一定要看 `driver_detail=...` 这段。
- Windows / Linux 的 clidriver 完全不同包，**别拿 Linux 的去 Windows 用**。

---

## OceanBase

OceanBase 有两种兼容模式，**驱动按底层协议走**：

### MySQL 兼容模式（最常用）
- 用 `pymysql` 当 MySQL 连，端口默认 `2881`。
- DDL / SQL 语法跟 MySQL 高度一致，sqlglot dialect 用 `mysql` 即可。
- 已知差异：OceanBase 有 tenant 概念，连接 user 形如 `<user>@<tenant>#<cluster>`，密码、host 跟 MySQL 一样。
- **当前内部 `DatabaseType.MYSQL` 直接可用**，无需新增 enum。

### Oracle 兼容模式
- 用 `oracledb` thin 模式连，端口同样 `2881`。
- service_name 通常是 tenant 名。
- sqlglot dialect 用 `oracle`。
- **当前没有 `DatabaseType.ORACLE` 默认装驱动**，requirements.txt 里 `oracledb` 是注释掉的，要现场启用。

### 离线打包风险
- MySQL 模式：零额外风险，跟普通 MySQL 一致。
- Oracle 模式：跟 Oracle 一致（thin 模式无 instantclient 依赖 → 简单；thick 模式同上）。
- 血缘里的方言路由别名 `ob_mysql` / `ob_oracle` 已经在 `app/lineage/dialects.py` `_resolve_dialect()` 映射好，前端给方言下拉框传 `ob_mysql` 即可。

---

## 离线打包通用清单

```text
DataOpsStudio-win-offline-X.Y.Z.zip
├── README_OFFLINE.md
├── install.bat / start.bat
├── wheels/
│   ├── fastapi-*.whl / uvicorn-*.whl / pydantic-*.whl / ...
│   ├── pymysql-*.whl + cryptography-*.whl       ← 永远带
│   ├── dmPython-*-cp{Python版本}-...-{OS}.whl   ← DM 客户必带
│   ├── oracledb-*.whl                           ← Oracle 客户必带
│   └── ibm_db-*.whl                             ← DB2 客户必带
├── clidriver/                                   ← DB2 独有，~30MB
├── instantclient_XX_X/                          ← Oracle thick 客户独有，~80MB
└── static/spa/                                  ← 前端 build 产物
```

### 打包前要问清楚的 3 个事
1. **目标机器 Python 版本？** dmPython / ibm_db / oracledb 都是 C 扩展，ABI 必须精确匹配。
2. **目标 OS / 架构？** Windows x64 / Linux x86_64 / ARM 都不一样。
3. **要用哪些 DB？** 不用的就别带 wheel，离线包体积能省一半。

### 打包后必跑的本地验证
```powershell
.\scripts\build_offline_windows.ps1
# 解压输出到临时目录 → install.bat → start.bat → curl localhost:8010/api/drivers
# 看 available 字段每个目标 DB 都是 true 才算齐
```

---

## 驱动可用性自检接口

应用启动时不会真去 connect 数据库，只 import 检测：

```bash
curl -fsS http://localhost:8010/api/drivers | jq
```

返回示例（key 是 `DatabaseType` enum 的 value，大小写跟 `app/models/common.py` 一致）：
```json
{
  "DM":     {"available": true,  "installed_modules": ["dmPython"],  "candidate_modules": ["dmPython"]},
  "MySQL":  {"available": true,  "installed_modules": ["pymysql"],   "candidate_modules": ["pymysql","MySQLdb"]},
  "Oracle": {"available": false, "installed_modules": [],            "candidate_modules": ["oracledb","cx_Oracle"]},
  "DB2":    {"available": false, "installed_modules": [],            "candidate_modules": ["ibm_db_dbi","ibm_db"]}
}
```

`available=false` 的 DB 在 UI 里仍能配数据源，但「测试连接」会报 `RuntimeError: <db> driver is not installed`。

---

## 应急排查

| 症状 | 大概率原因 | 处置 |
|------|---------|------|
| `ImportError: DLL load failed`（DB2 / dmPython） | VC++ 缺失 / clidriver PATH 没配 / wheel ABI 错配 | 装 VC redistributable + 设 `IBM_DB_HOME` + 确认 wheel Python 版本 |
| `'cryptography' package is required`（MySQL） | pymysql 装了但没装 cryptography | `pip install cryptography` |
| `ORA-12541: TNS:no listener` | Oracle 服务没起 / port 错 / 防火墙 | 确认 listener + telnet host port |
| dmPython `connect` 卡死 | DM 服务器端 max_connections 满 / 网络隔离 | 看 DM 服务端日志，调连接池 `max_size` |
| ibm_db 错就一句 "returned a result with an exception set" | DB 真错被 Python wrapper 吞 | 看 `_extract_driver_error_detail` 拼的 `driver_detail=...` 段（应用日志里）|
