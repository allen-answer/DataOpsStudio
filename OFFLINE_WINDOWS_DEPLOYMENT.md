# DataOps Studio Windows 离线部署文档

## 1. 适用范围

本文档按“干净 Windows 环境”编写，即目标机器只有操作系统和 Python，不假设已经安装项目依赖包。

目标环境：

- Windows 10 / Windows Server 或更高版本
- Python 3.12
- 禁止使用 Docker
- 目标机器无法访问公网
- 使用本地 Python 直接启动服务
- 生产运行不需要 Node.js、npm 或 Vue；SPA 前端已构建到 `static\spa`

---

## 2. 必需 Python 包

项目基础运行依赖如下：

```text
fastapi
uvicorn[standard]
jinja2
python-multipart
pydantic
pandas
openpyxl
sqlglot
```

这些依赖统一记录在：

```text
requirements.txt
```

说明：

- `fastapi`、`uvicorn`、`jinja2`、`python-multipart`、`pydantic` 用于 Web 服务和页面表单。
- `pandas`、`openpyxl` 用于 Excel 导出。
- `sqlglot` 用于 SQL 血缘分析、SQL 格式化、输出字段提取和候选 key 提示。

---

## 3. 可选数据库驱动

按实际使用的数据库安装对应驱动：

```text
MySQL / OceanBase MySQL：pymysql
Oracle：oracledb 或 cx_Oracle
DB2：ibm_db
DM：dmPython
```

说明：

- 只需要安装实际使用数据库对应的驱动。
- OceanBase MySQL 模式按 MySQL 配置，推荐使用 `pymysql`。
- MySQL 8 默认 `caching_sha2_password` 认证时，`pymysql` 通常还需要 `cryptography`。
- DB2 使用 `ibm_db` 时，需要确保 `clidriver` 可用。
- DM 的 `dmPython` 通常来自达梦安装介质或官方驱动包，需要与现场 Python 版本匹配。

---

## 4. 在线环境准备离线依赖包

如果离线包内已经包含完整 `wheels` 目录，可以跳过本节。

如果需要自行准备依赖包，请在一台可联网且 Python 版本一致的机器上执行：

```bat
python -m pip download -r requirements.txt -d wheels
```

如需同时准备数据库驱动，可按实际需要追加：

```bat
python -m pip download pymysql cryptography -d wheels
python -m pip download oracledb -d wheels
python -m pip download ibm_db -d wheels
```

DM 的 `dmPython` 通常需要从达梦安装介质或官方驱动包获取，不能保证能从 PyPI 直接下载。

准备完成后，应将以下内容一起拷贝到离线机器：

```text
DataOpsStudio 项目目录
wheels 目录
requirements.txt
```

---

## 5. 部署目录

建议解压到固定目录，例如：

```text
D:\DataOpsStudio
```

目录结构示例：

```text
DataOpsStudio
├── app
├── config
├── logs
├── results
├── static
├── templates
├── wheels
├── main.py
├── requirements.txt
├── install_dependencies_offline.bat
└── start.bat
```

---

## 6. 离线安装依赖

进入项目目录，双击：

```text
install_dependencies_offline.bat
```

或在命令行执行：

```bat
cd /d D:\DataOpsStudio
python -m pip install --no-index --find-links=.\wheels -r requirements.txt
```

安装成功后可验证基础依赖：

```bat
python -c "import fastapi, uvicorn, jinja2, multipart, pydantic, pandas, openpyxl, sqlglot; print('ok')"
```

如需安装数据库驱动，可在同一目录继续执行对应命令：

```bat
python -m pip install --no-index --find-links=.\wheels pymysql cryptography
python -m pip install --no-index --find-links=.\wheels oracledb
python -m pip install --no-index --find-links=.\wheels ibm_db
```

如需验证数据库驱动：

```bat
python -c "import pymysql, cryptography; print('pymysql ok')"
python -c "import oracledb; print('oracledb ok')"
python -c "import ibm_db; print('ibm_db ok')"
```

按现场实际数据库选择验证即可。

---

## 7. 启动服务

双击运行：

```text
start.bat
```

`start.bat` 会启动 8000 端口服务，并自动打开 SPA 首页。

或命令行执行：

```bat
cd /d D:\DataOpsStudio
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

如果浏览器没有自动弹出，可手动访问：

```text
http://127.0.0.1:8000/spa
```

如果局域网其他机器访问，需要使用部署机器 IP：

```text
http://部署机器IP:8000/spa
```

经典兼容页面仍保留在：

```text
http://部署机器IP:8000
```

---

## 8. 配置和数据文件

项目不使用配置数据库，所有配置和结果均保存在本地文件。

```text
config/datasources.json：数据源配置
config/tasks.json：对比任务配置
results/：JSON / Excel 对比结果
logs/app.log：运行日志
```

备份或迁移时，重点保留：

```text
config
results
logs
```

---

## 9. 部署后验证

建议按以下顺序验证：

1. 打开 DataOps Studio：`http://127.0.0.1:8000/spa`
2. 进入“数据源管理”，确认 DM、DB2、OceanBase、Oracle 所需数据库驱动可用
3. 新增数据源
4. 点击数据源“测试”，确认显示 `连接成功`
5. 新增对比任务
6. 在任务编辑区使用源 SQL / 目标 SQL 的“格式化”和“提取字段”验证 SQL 辅助
7. 使用“预览源 / 预览目标”确认 SQL 可查，并确认预览结果区域可查看较长 JSON 内容
8. 在任务配置中确认 `查询分块行数` 可设置；如要验证大结果集优化，可在高级配置中开启“流式分块对比”，并确保源/目标 SQL 已按主键排序
9. 执行任务，下载 JSON / Excel 结果
10. 使用“后台执行”验证异步状态；如任务仍在运行，可点击“取消后台任务”
11. 打开“执行历史”，确认结果可重新下载
12. 在执行历史中勾选多个结果和 sheet，验证可合并导出到一个 Excel
13. 打开“SQL 血缘分析”，粘贴 SQL 或上传 `.sql/.txt` 文件验证
14. 如 SQL 使用 `SELECT *`，可上传一个或多个 Schema JSON / SQL / TXT，或上传 Schema ZIP 包验证字段展开
15. 建议使用包含 `${v_qmrq_m}` 等变量、多段 `INSERT INTO ... SELECT ...`、CTE、复杂子查询或 `EXECUTE IMMEDIATE` / `sp_executesql` 动态 SQL 的 ETL 脚本验证血缘增强能力
16. 确认页面展示血缘图、“脚本变量”、来源表、字段血缘和落表字段映射
17. 在“SQL 血缘分析”页面点击“导出 Excel”或“导出 JSON”，验证血缘结果可导出；Excel 应包含 `脚本变量` sheet
18. 打开“多脚本分析”或访问 `http://127.0.0.1:8000/lineage/batch`
19. 上传多个 `.sql` / `.txt`，或上传包含脚本的 `.zip`，验证脚本清单、表级数据流、跨脚本依赖、流程图和风险提示
20. 在“数据源管理”页面验证配置导入/导出；如需迁移配置，可用导出的 JSON 进行配置导入

Docker 本地测试时注意：

- 如果 MySQL 容器与应用容器不在同一个 Docker 网络，应用容器内可能无法通过 MySQL 容器名访问。
- 例如 MySQL 容器名为 `mysql-db`，但应用容器在 `data_compare_tool_default` 网络、MySQL 在默认 `bridge` 网络时，`mysql-db` 会解析失败。
- 这种情况下可使用宿主机转发地址，例如 Docker Desktop 下的 `host.docker.internal:3306`，或把两个容器加入同一个 Docker 网络。

---

## 10. 常见问题

### 10.1 提示缺少 Python 包

确认当前 Python 是否为 3.12：

```bat
python --version
```

查看已安装包：

```bat
python -m pip list
```

重新从本地 wheels 安装：

```bat
python -m pip install --no-index --find-links=.\wheels -r requirements.txt
```

如果仍失败，通常说明 `wheels` 目录中缺少某个依赖包，需要在联网环境重新下载完整 wheel。

### 10.2 `python` 命令不可用

编辑 `start.bat` 和 `install_dependencies_offline.bat`，将 `python` 替换为完整路径，例如：

```text
C:\Python312\python.exe
```

### 10.3 数据源测试失败

页面会显示：

```text
连接失败：错误信息
```

排查顺序：

1. 数据库驱动是否安装
2. 数据库地址和端口是否能访问
3. 用户名、密码、库名或服务名是否正确
4. 防火墙是否放行
5. Oracle / DB2 / DM 是否需要额外客户端或环境变量

### 10.4 血缘分析或 SQL 辅助失败

血缘分析和 SQL 辅助依赖 `sqlglot`。如果该功能失败，先按完整依赖检查：

```bat
python -c "import fastapi, uvicorn, jinja2, multipart, pydantic, pandas, openpyxl, sqlglot; print('ok')"
```

如检查失败，从完整 `wheels` 目录重新安装 `requirements.txt`：

```bat
python -m pip install --no-index --find-links=.\wheels -r requirements.txt
```

血缘分析属于静态解析，不连接数据库、不执行 SQL。当前支持识别常见变量和占位符，例如：

```text
${v_qmrq_m}
:biz_date
@biz_date
```

也支持从 ETL 脚本中提取多段 `INSERT INTO ... SELECT ...`，从 `CREATE PROCEDURE` / `CREATE FUNCTION` 过程体中提取可分析的 `INSERT SELECT`，并从 `EXECUTE IMMEDIATE`、`sp_executesql` 等动态 SQL 字符串中提取可分析 SQL。Schema 元数据可辅助展开 `SELECT *`，单脚本和多脚本均支持一次上传多个 JSON / SQL / TXT 元数据文件，也支持上传包含元数据文件的 ZIP 包；如果脚本大量运行时拼接表名/字段名或依赖条件分支，字段级血缘仍可能只能给出有限结果，建议将关键落表 SQL 展开为显式字段后再分析。

多脚本 ETL 流程分析入口为：

```text
http://127.0.0.1:8000/lineage/batch
```

该页面支持多选 `.sql` / `.txt`，也支持上传包含 `.sql` / `.txt` 的 `.zip`。当前主要输出表级数据流和跨脚本依赖，适合先确认整个脚本目录的上下游关系；字段级全链路追踪仍建议结合单脚本血缘结果查看。

Schema 元数据可用于辅助展开 `SELECT *`，支持多个 JSON / SQL / TXT 文件，也支持 ZIP 包。多个元数据文件会按表名合并字段。JSON 支持：

```json
{
  "ods.customer": ["cust_id", "cust_name"]
}
```

也支持：

```json
{
  "tables": [
    {"table": "ods.customer", "columns": ["cust_id", "cust_name"]}
  ]
}
```

### 10.5 提取字段后没有候选 key

候选 key 是静态启发式提示，不连接数据库、不读取表结构。当前主要根据字段名判断，例如：

```text
id
uuid
*_id
*_no
*_num
*_code
*_cd
包含 num 的字段
```

如果 SQL 使用：

```sql
SELECT * FROM table_name
```

静态解析只能识别到 `*`，无法展开真实表字段，因此不会给出准确候选 key。建议改成显式字段：

```sql
SELECT CLIENT_ID, FUND_ACC_NO, SEC_CODE FROM table_name
```

### 10.6 字段映射为空时如何比较

双 SQL 模式下，如果字段映射为空，且源 SQL 与目标 SQL 返回列数一致，系统会按查询字段顺序自动映射：

```text
源第 1 列 -> 目标第 1 列
源第 2 列 -> 目标第 2 列
源第 3 列 -> 目标第 3 列
```

如果两边字段顺序不同，或字段含义不是一一对应，应手工填写字段映射。

---

## 11. 升级增量热修复包

如果已部署过全量包，只需要覆盖增量热修复包。

步骤：

1. 停止正在运行的服务
2. 解压增量热修复包到项目根目录
3. 选择覆盖同名文件
4. 如热修复包新增了依赖，再执行 `install_dependencies_offline.bat`
5. 重新运行 `start.bat`

增量包目录结构是相对于项目根目录的，例如：

```text
app/compare/engine.py
templates/index.html
HOTFIX_NOTES_20260424.md
PROJECT_SUMMARY.md
OFFLINE_WINDOWS_DEPLOYMENT.md
```
