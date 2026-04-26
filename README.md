# DataOps Studio

一个面向离线环境的 DataOps 工具台，覆盖多数据库数据对比、SQL 血缘、ETL 多脚本流程分析、Schema 元数据辅助和结果导出。所有配置保存为本地 JSON 文件，不使用配置数据库。

## 功能

- 数据源配置管理：`config/datasources.json`
- 对比任务配置管理：`config/tasks.json`
- 支持 DM、MySQL、Oracle、DB2
- 驱动检测接口：`GET /api/drivers`
- 单 SQL / 双 SQL 对比模式
- 人工指定 `key_columns`
- 数据源连通性测试
- 对比规则：忽略字段、字段映射、数值误差容忍、字符串归一化
- 字段映射为空时，双 SQL 模式支持按源/目标查询字段顺序自动映射
- 查询行数上限和 Excel 导出行数上限
- 任务后台执行和状态查询
- 后台任务支持取消；取消为协作式取消，正在执行的数据库查询会在当前阶段结束后停止后续流程
- 预览源/目标数据，预览结果区域支持较大 JSON 查看和手动调整高度
- 执行历史列表、重新下载和删除结果
- 历史结果支持多选 sheet 合并导出到一个 Excel
- 输出 `only_source`、`only_target`、`diff`、`same`
- DataOps Studio SPA：基于 Vue 构建，生产环境只需 `static/spa/` 静态构建产物，不需要安装 Node/Vue
- 数据源管理主页：集中展示数据源、数据库驱动检测、新增和测试连接
- 数据对比任务工作台：左侧任务列表、右侧任务详情
- 导出 JSON 和 Excel
- SQL 血缘分析：静态解析来源表、来源字段、脚本变量、过滤、分组、联合和落表映射
- 多脚本 ETL 流程分析：批量上传多个 `.sql` / `.txt`，或上传 `.zip`，汇总脚本清单、表级数据流、跨脚本依赖和风险提示
- SQL 血缘图形化展示：单脚本和多脚本页面展示表级 `来源表 -> 目标表` 流程图
- Schema 元数据导入：单脚本和多脚本均支持上传多个 JSON / SQL / TXT 元数据文件，也支持上传包含元数据文件的 ZIP；用于展开 `SELECT *`，并增强动态 SQL / 过程体中的字段级血缘
- 查询分块读取：任务可配置 `fetch_chunk_size`，大结果集查询时分批读取并更新后台执行进度
- 流式分块对比：任务可开启 `stream_compare`，按主键有序结果集做边读边归并，减少源/目标全量结果集常驻内存
- 配置导入/导出：导出数据源和任务配置 JSON，支持从 JSON 覆盖导入
- SQL 辅助：格式化 SQL、只读校验、输出字段提取、候选 key 提示

## 启动

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

浏览器打开：

```text
http://127.0.0.1:8000/spa
```

Windows 离线包中的 `start.bat` 会启动 8000 端口服务，并自动打开上述 SPA 首页。

经典 Jinja 页面仍保留在：

```text
http://127.0.0.1:8000
```

## Docker 启动

项目已包含 `Dockerfile` 和 `docker-compose.yml`。在项目目录运行：

```bash
docker compose up --build -d app
```

浏览器打开 `http://127.0.0.1:8000`。

常用命令：

```bash
docker compose logs -f app
docker compose exec app bash
docker compose down
```

`config`、`logs`、`results` 会挂载到宿主机当前目录，容器重启后配置、日志和导出结果仍会保留。

Dockerfile 已配置容器内依赖下载走清华源：

- Debian 软件包：`https://mirrors.tuna.tsinghua.edu.cn/debian`
- PyPI 软件包：`https://pypi.tuna.tsinghua.edu.cn/simple`

注意：清华 Docker CE 镜像是 Docker 安装包仓库，不是 Docker Hub 镜像仓库。因此 `python:3.12-slim` 这个基础镜像仍需要 Docker 能访问 Docker Hub，或需要你在 Docker Desktop/daemon 中配置其它可用的 Docker Hub registry mirror。

## Docker 中联通 Codex

`docker-compose.yml` 额外提供了一个 `codex` 开发容器，它和应用容器使用同一份代码挂载目录。这样 Codex 在宿主机修改文件后，Docker 容器能立即读取到；你也可以进入容器运行测试或调试命令：

```bash
docker compose up --build -d codex
docker compose exec codex bash
python -m unittest discover -s tests
```

如果后续需要在容器内使用 OpenAI/Codex 相关 CLI，可在宿主机设置 `OPENAI_API_KEY` 后再启动：

```bash
export OPENAI_API_KEY="你的 key"
docker compose up -d codex
```

如需连接真实数据库，请确保容器网络能访问数据库地址；按实际数据库类型在 `requirements.txt` 中启用并安装对应驱动，例如 `pymysql`、`oracledb`、`ibm_db` 或 `dmPython`。

Windows 离线环境建议使用 Python 3.12。如果机器上已有 Python 环境，并且依赖包已经在该环境中，直接双击：

```text
start.bat
```

或命令行运行：

```bat
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

如果离线机器不能使用 `pip`，必须提前保证当前 Python 环境中已经包含 `requirements.txt` 里的依赖。可以在另一台同版本 Windows/Python 环境中准备虚拟环境，或由管理员统一安装到目标 Python 环境。

在有网的 Windows 机器上安装基础依赖：

```bat
python -m pip install fastapi "uvicorn[standard]" jinja2 python-multipart pydantic pandas openpyxl
```

按实际数据库类型再安装驱动：

```bat
python -m pip install pymysql oracledb ibm_db
```

DM 达梦的 `dmPython` 通常需要使用达梦数据库安装目录或官方驱动包里的 wheel/安装包，按现场版本匹配安装。

如果允许提前准备离线依赖包，可使用：

```bat
py -m venv .venv
.venv\Scripts\activate
pip install --no-index --find-links=.\wheels -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

`wheels` 目录需要提前在有网络的同版本 Windows/Python 3.12 环境下载：

```bat
pip download -r requirements.txt -d wheels
```

数据库驱动按实际需要额外准备离线包：

- DM：`dmPython`，通常来自达梦安装介质或官方驱动包
- MySQL：`pymysql`
- Oracle：`oracledb`，如使用 thick 模式还需要 Oracle Instant Client
- DB2：`ibm_db`，Windows 离线安装时注意匹配 Python 版本和位数

## SQL 模式

- 单 SQL：源库和目标库执行同一条 `source_sql`
- 双 SQL：源库执行 `source_sql`，目标库执行 `target_sql`

`key_columns` 需要手工填写，例如 `id` 或 `tenant_id,id`。同一侧结果中 key 不能重复。

双 SQL 模式下，如果源 SQL 和目标 SQL 字段名不一致，可以在字段映射中填写：

```text
源字段 -> 目标字段
```

字段映射也支持 `=>`、`→`、`:`、`：` 分隔。若字段映射为空，且源/目标查询结果列数一致，系统会按查询字段顺序自动映射，例如源第 1 列对应目标第 1 列，源第 2 列对应目标第 2 列。

任务可选对比规则：

- `ignore_columns`：忽略字段，例如 `etl_time,update_time`
- `column_mappings`：字段映射，例如 `src_amt->target_amount`，也适用于目标侧 key 字段
- `numeric_tolerance`：数值误差容忍，例如 `0.01`
- `trim_strings`：字符串比较前去除前后空格
- `case_insensitive`：字符串比较忽略大小写
- `empty_as_null`：空字符串按空值处理

任务还支持 `max_rows` 查询行数上限和 `export_max_rows` Excel 导出行数上限，用于避免超大结果集占用过多内存和导出时间。

为降低数据库风险，执行前会校验 SQL：

- 只允许 `SELECT` 或 `WITH` 查询
- 禁止多语句
- 禁止 `SELECT ... FOR UPDATE`
- 禁止 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`DROP`、`ALTER`、`TRUNCATE` 等写入/DDL 关键字

默认连接超时为 10 秒；MySQL/OceanBase MySQL 模式默认读写超时为 300 秒。MySQL/OceanBase 可在数据源 JSON 的 `extra` 中覆盖 `connect_timeout`、`read_timeout`、`write_timeout`。

## 日志

运行日志写入：

```text
logs/app.log
```

日志会记录驱动检测、任务开始、SQL 查询开始/完成、任务汇总、异常堆栈和结果文件名。单个日志文件最大 5MB，最多保留 5 个历史文件。日志不会记录数据库密码。

## SQL 血缘分析与 SQL 辅助

血缘分析入口：

```text
/lineage
```

该功能基于 `sqlglot` 静态解析 SQL，不连接数据库、不执行 SQL。支持基础 `SELECT`、`JOIN`、`WHERE`、`GROUP BY`、聚合字段，以及 `UNION` / `UNION ALL` 分支识别。

血缘分析也支持常见 ETL 脚本形态：

- 识别脚本变量和占位符，例如 `${v_qmrq_m}`、`:biz_date`、`@biz_date`
- 跳过 `DELETE`、`COMMIT` 等非血缘主体语句，提取脚本中的多段 `INSERT INTO ... SELECT ...`
- 从 `CREATE PROCEDURE` / `CREATE FUNCTION` 过程体中提取可分析的 `INSERT SELECT`
- 从 `EXECUTE IMMEDIATE`、`sp_executesql` 等动态 SQL 字符串中提取可分析的 `INSERT SELECT`
- 展开 CTE、复杂派生表、嵌套子查询和 `UNION` 子查询中的字段来源
- 落表字段映射中显示目标字段、来源字段、来源表、变量和处理表达式
- 血缘结果可导出 JSON / Excel，Excel 中包含脚本变量、字段血缘和落表字段映射等 sheet

血缘分析页面支持上传 Schema 元数据，用于在静态解析时展开 `SELECT *`。单脚本和多脚本分析均支持一次选择多个 `.json`、`.sql`、`.txt` 元数据文件，也支持上传 `.zip` 元数据包。多个元数据文件会按表名合并字段。

JSON 支持两种常见格式：

```json
{
  "ods.customer": ["cust_id", "cust_name"]
}
```

或：

```json
{
  "tables": [
    {"table": "ods.customer", "columns": ["cust_id", "cust_name"]}
  ]
}
```

SQL / TXT 支持 `CREATE TABLE` DDL，例如：

```sql
create table ods.customer (
  cust_id varchar2(32),
  cust_name varchar2(100)
);
```

也支持简单文本格式：

```text
ods.customer,cust_id
ods.customer,cust_name
```

或：

```text
TABLE: ods.customer
cust_id
cust_name
```

注意：静态解析不会连接数据库读取真实表结构。Schema 元数据可提升 `SELECT *` 和部分动态 SQL 字符串的字段级准确度；如果 SQL 在运行时大量拼接、条件分支才决定表名/字段名，仍建议补充 Schema 并尽量把关键落表 SQL 展开为完整 `INSERT ... SELECT`。

血缘分析页面保留 SQL 格式化能力，用于整理当前文本框内 SQL。

多脚本 ETL 流程分析入口：

```text
/lineage/batch
```

该页面用于分析一个脚本目录或脚本包的整体数据流。当前支持多选上传 `.sql` / `.txt`，也支持上传包含 `.sql` / `.txt` 的 `.zip`。

Schema 元数据同样支持多文件：可一次选择多个 `.json` / `.sql` / `.txt`，也可以上传包含这些文件的 `.zip`。系统会逐个解析并按表名合并字段，辅助展开 `SELECT *`。

输出包括：

- 脚本清单：每个文件的语句数、读取表、写入表、变量和解析提示
- 表级数据流：来源表、目标表、所属脚本、语句序号
- 跨脚本依赖：脚本 A 写出的中间表被脚本 B 读取
- 风险提示：解析失败、`SELECT *`、疑似动态 SQL、多脚本写同一目标表、外部源表、最终产物

第一阶段以表级流程和脚本依赖为主；字段级跨脚本追踪会保留单脚本落表字段映射结果，后续可继续增强为按表/字段的全链路查询。

首页任务编辑区的源 SQL / 目标 SQL 支持：

- 格式化 SQL
- 提取输出字段
- 基于字段名给出候选 key 提示

候选 key 是静态启发式提示，不会自动决定主键。当前会优先提示字段名类似 `id`、`uuid`、`*_id`、`*_no`、`*_num`、`*_code`、`*_cd` 或包含 `num` 的字段，例如 `CLIENT_ID`、`FUND_ACC_NO`、`SEC_CODE`。

注意：如果 SQL 使用 `SELECT *`，静态解析只能识别到 `*`，无法展开真实表字段，也无法准确推荐候选 key。需要显式写出字段名，或先使用预览确认返回列。

## 接口

- `GET /api/drivers`：检测 DM、MySQL、Oracle、DB2 驱动是否可导入
- `GET /config/export`：导出数据源和任务配置 JSON
- `POST /config/import`：导入数据源和任务配置 JSON
- `GET /api/datasources` / `POST /api/datasources` / `PUT /api/datasources/{id}` / `DELETE /api/datasources/{id}`
- `POST /api/datasources/{id}/test`：测试数据源连通性
- `GET /api/tasks` / `POST /api/tasks` / `PUT /api/tasks/{id}` / `DELETE /api/tasks/{id}`
- `POST /api/tasks/{id}/run`：执行对比并写入 `results` 目录
- `POST /api/tasks/{id}/run-async`：后台执行任务
- `GET /api/runs/{job_id}`：查询后台执行状态
- `POST /api/runs/{job_id}/cancel`：取消后台执行任务
- `POST /api/tasks/{id}/preview`：执行前预览源或目标前 N 行
- `GET /api/history`：结果历史
- `POST /api/sql/assist`：SQL 格式化、方言转换、只读校验、输出字段和候选 key 提取

## 验证

核心逻辑不依赖真实数据库，可直接运行：

```bash
python -m unittest discover -s tests
```

Docker 环境中也可执行：

```bash
docker compose exec app python -m unittest discover -s tests -v
```
