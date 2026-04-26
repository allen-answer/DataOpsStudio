# DataOps Studio Windows 离线部署说明

适用环境：

- Windows
- Python 3.12 已安装
- 禁止使用 Docker
- 目标机器无法访问公网
- 使用本地 Python 环境直接启动服务
- 生产运行不需要 Node.js、npm、Vue 或 Docker；前端已构建到 `static\spa`

## 1. 解压

将压缩包解压到目标机器，例如：

```text
D:\DataOpsStudio
```

## 2. 离线安装依赖

如果目标环境已经由管理员安装好项目所需 Python 包和数据库驱动，可以跳过本节。

双击运行：

```text
install_dependencies_offline.bat
```

或手工执行：

```bat
cd /d D:\DataOpsStudio
python -m pip install --no-index --find-links=.\wheels -r requirements.txt
```

验证基础依赖：

```bat
python -c "import fastapi, uvicorn, jinja2, multipart, pydantic, pandas, openpyxl, sqlglot; print('ok')"
```

数据库驱动按现场实际数据库额外安装，例如：

- OceanBase MySQL：`pymysql`，MySQL 8 默认认证通常还需要 `cryptography`
- OceanBase Oracle：通常走 `oracledb`
- Oracle：`oracledb`
- DB2：`ibm_db`
- DM：`dmPython`

## 3. 启动

双击运行：

```text
start.bat
```

`start.bat` 会启动 8000 端口服务，并自动打开 SPA 首页：

```text
http://127.0.0.1:8000/spa
```

经典兼容页面仍可访问：

```text
http://127.0.0.1:8000
```

## 4. 配置文件

配置和结果都保存在本地文件：

- `config\datasources.json`：数据源配置
- `config\tasks.json`：对比任务配置
- `results\`：JSON / Excel 结果
- `logs\app.log`：运行日志

## 5. 数据库驱动

按实际数据库类型确认对应驱动已安装：

- MySQL / OceanBase MySQL：`pymysql`
- Oracle：`oracledb` 或 `cx_Oracle`
- DB2：`ibm_db`
- DM：`dmPython`

DB2 如使用 `ibm_db`，`start.bat` 会尝试加载常见 `clidriver` 路径。

## 6. 部署后验证

建议按以下顺序验证：

1. 打开 SPA 首页：`http://127.0.0.1:8000/spa`
2. 进入“数据源管理”，确认 DM、DB2、OceanBase、Oracle 所需数据库驱动可用
3. 新增数据源，并点击“测试”确认显示 `连接成功`
4. 新增或编辑任务，使用 SQL 区域的“格式化”和“提取字段”
5. 使用“预览源 / 预览目标”确认 SQL 可查，并确认预览结果区域可查看较长 JSON 内容
6. 确认任务配置中的 `查询分块行数` 可设置；如要验证大结果集优化，可在高级配置中开启“流式分块对比”，并确保源/目标 SQL 已按主键排序
7. 执行任务，下载 JSON / Excel 结果
8. 使用“后台执行”验证异步状态；如任务仍在运行，可点击“取消后台任务”
9. 打开“执行历史”，验证按任务筛选和结果重新下载
10. 如需合并结果，在执行历史中勾选多个结果和 sheet 后导出
11. 打开“单脚本血缘”，粘贴或上传 `.sql/.txt` 脚本验证
12. 如 SQL 使用 `SELECT *`，可上传一个或多个 Schema JSON / SQL / TXT，或上传 Schema ZIP 包，验证字段展开
13. 建议使用包含 `${v_qmrq_m}` 等变量、多段 `INSERT INTO ... SELECT ...`、CTE、复杂子查询或 `EXECUTE IMMEDIATE` / `sp_executesql` 动态 SQL 的 ETL 脚本验证
14. 确认页面展示血缘图，并可导出血缘 Excel；Excel 应包含 `脚本变量`、字段血缘和落表字段映射结果
15. 打开“多脚本分析”或访问 `http://127.0.0.1:8000/lineage/batch`
16. 上传多个 `.sql` / `.txt`，或上传包含脚本的 `.zip`，验证脚本清单、表级数据流、跨脚本依赖、流程图和风险提示
17. 多脚本分析中上传多个 Schema JSON / SQL / TXT，或上传 Schema ZIP 包，验证 `SELECT *` 字段展开
18. 验证配置导出；如需迁移配置，可用导出的 JSON 进行配置导入

## 7. 常见问题

如果 `start.bat` 提示缺包，先确认当前命令行的 `python` 是否是目标 Python 3.12：

```bat
python --version
python -m pip list
```

如果现场 Python 命令不是 `python`，可编辑 `start.bat` 和 `install_dependencies_offline.bat`，把 `python` 替换成完整路径，例如：

```text
C:\Python312\python.exe
```

如果 SQL 辅助提取字段后没有候选 key，请先确认 SQL 是否使用了 `SELECT *`。静态解析不会连接数据库读取表结构，因此需要显式写出字段名才能准确提示候选 key。

血缘分析同样是静态解析，不连接数据库、不执行 SQL。当前支持 `${v_qmrq_m}`、`:biz_date`、`@biz_date` 等变量识别，也支持从 ETL 脚本、存储过程体、`EXECUTE IMMEDIATE` 和 `sp_executesql` 动态 SQL 字符串中提取可分析的 `INSERT SELECT`。Schema 元数据可辅助展开 `SELECT *`，支持 JSON、`CREATE TABLE` SQL 和简单 TXT；如果脚本运行时拼接表名/字段名，建议先展开为显式字段再分析。

多脚本 ETL 流程分析在 SPA 的“多脚本分析”页面中使用；经典入口为 `http://127.0.0.1:8000/lineage/batch`。该功能支持多选 `.sql` / `.txt`，也支持上传包含脚本的 `.zip`，用于汇总脚本目录里的表级上下游和跨脚本依赖。

Schema 元数据可用于辅助展开 `SELECT *`。单脚本和多脚本分析均支持一次选择多个 JSON / SQL / TXT 文件，也支持上传包含元数据文件的 ZIP 包。JSON 格式可以是：

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

完整部署说明见：

```text
OFFLINE_WINDOWS_DEPLOYMENT.md
```
