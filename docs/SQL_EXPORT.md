# SQL 工作台结果导出(v0.5+)

把 SQL 工作台跑出来的 result grid 导出成 4 种格式:CSV / Excel / JSON /
SQL Insert。短同步 + 长查询自动异步,文件落 `results/sql_exports/`。

## 4 种格式

| 格式 | 扩展名 | MIME | 用途 |
|---|---|---|---|
| **CSV** | `.csv` | text/csv (UTF-8 BOM) | Excel 双击直接看;BI 导入 |
| **Excel** | `.xlsx` | xlsx | 含 native 类型(日期 / 数字),给非工程师同事 |
| **JSON** | `.json` | application/json | 给程序员 / pipeline 处理 |
| **SQL Insert** | `.sql` | text/plain | 数据迁移 / 测试 fixture |

## API

### 提交导出

```bash
POST /api/sql-workbench/export
{
  "datasource_id": "ds-uuid",
  "sql": "SELECT id, name FROM users WHERE created_at > '2026-01-01'",
  "format": "csv",            # csv | excel | json | sql
  "title": "monthly_report",  # 可选,进文件名
  "max_rows": 100000          # 上限,服务端再 clamp [1, 1000000]
}
```

返回 envelope(同 execution 模型风格):

```json
{
  "export_id": "abc123...",
  "format": "csv",
  "status": "success",     // pending | running | success | failed
  "row_count": 5234,
  "file_size": 134567,
  "file_name": "prod-warehouse_monthly_report_20260526-153021_abc12345.csv",
  "download_url": "/api/sql-workbench/export/abc123.../download",
  "truncated": false,
  "error": null
}
```

短同步窗口 500ms 内完成直接返 success;**大结果**自动走异步:返
`status=running` + `export_id`,前端 poll `GET /api/sql-workbench/export/{id}`
直到终态,再调 download。

### 查状态

```bash
GET /api/sql-workbench/export/{export_id}
```

### 下载文件

```bash
GET /api/sql-workbench/export/{export_id}/download
```

返 `FileResponse`,Content-Disposition: attachment + filename。仅 export
owner 能下载(403)。文件 24 小时后 TTL cleanup(物理删除 + registry 清)。

## 数据类型处理

| 源类型 | CSV | Excel | JSON | SQL Insert |
|---|---|---|---|---|
| `None` | 空字段 | empty cell | `null` | `NULL` |
| `int / float` | 字面值 | native number | number | 字面值 |
| `Decimal` | 字符串(保精度) | `float()` | 字符串(保精度) | 字面值 |
| `datetime / date` | ISO 8601 | native datetime cell | ISO 字符串 | `'2026-05-26T12:30:00'` |
| `bool` | true/false | 1/0 | true/false | 1/0 |
| `bytes` | hex 字符串 | hex 字符串 | hex 字符串 | `X'<hex>'` |
| `str` | as-is | 见下方公式注入防御 | as-is | 单引号 `'` 转义 `''` |

## 公式注入防御(#13)

**Excel 专属**。`SELECT` 返回的字符串字段若以 `= + - @ \t \r` 开头,Excel /
WPS / Google Sheets 都会把它当成公式执行 — 攻击者可在数据库埋
`=SYSTEM("rm -rf")` 等待用户导出。

防御:在写 cell 前 prepend `'`,让 Excel 当成纯文本。CSV / JSON / SQL 格式
本身不含公式语义,**不**做这层防御。

```
"=SUM(A:A)"   → 写入 Excel 为  "'=SUM(A:A)"
"+CMD()"      →                 "'+CMD()"
"@evil"       →                 "'@evil"
"-1+1"        →                 "'-1+1"
"\t injected" →                 "'\t injected"
```

`@danger.com` 这种 email 也会被加 `'`,代价 < 安全。

## 文件名

```
<datasource-name>_<user-title>_<YYYYMMDD-HHMMSS>_<export-id-8>.<ext>
```

示例:`prod-warehouse_monthly_report_20260526-153021_abc12345.csv`

- 字段先经 `_slugify` 处理:`[A-Za-z0-9_-]` 之外全替换为 `_`,长度截 40
- `title` 留空时填 `untitled`
- export-id 前 8 位防同名

## 权限矩阵

| 角色 | 提交 export | 查 / 下载自己的 export | 查 / 下载他人的 export |
|---|---|---|---|
| viewer | ❌ 401/403 | ❌ | ❌ |
| editor | ✅ | ✅ | ❌ 403 |
| admin | ✅ | ✅ | ❌ 403(包括 admin 自己,因为按 user_id 隔离) |

跨 `project_id` 由 `require_datasource_access` 拦 — 用户连 ds 都看不到的话
连 export 都提交不进来。

## 配额 / 限制

| 项 | 上限 |
|---|---|
| max_rows | 1,000,000 行(服务端硬 cap) |
| 文件保留 | 24 小时,过期清磁盘 + registry |
| 并发 worker | 2(`ThreadPoolExecutor` 池) |
| 短同步 sync_wait | 500 ms,过点未完返 running |
| 前端 poll 上限 | 10 分钟(1200 × 500ms) |

## 测试

`tests/test_sql_workbench_export.py` 16 case:
- 4 种 format happy path(CSV / Excel / JSON / SQL Insert)
- NULL / datetime / Decimal 序列化
- Excel 公式注入(`=` `+` `@` `-` 四种 prefix)
- 权限(viewer 拒 + 跨用户下载拒)
- sql_guard 拦 DML
- 异步路径(慢 fetch → pending → poll → success)
- 文件名含 datasource + title + timestamp
- 文件被 cleanup 后下载返 410

## 审计

- 走 `AuditLogMiddleware` 落 `logs/audit.jsonl`(所有 POST /api/* 自动)
- 完成 + 下载额外 `logger.info`,含 export_id / user / ds / format / rows / bytes
  / file_name —— admin 可在 logs 里 grep `sql export` 查谁导了多少数据
