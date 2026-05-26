# DataOps Studio 发布包记录

仓库 `dist/` 目录历次产物索引。新包从顶部加。

---

## 2026-05-26

### `DataOpsStudio-portable-db-drivers-helper.zip`
**2.7 KB · helper script · git `685049d`**

Helper for portable package: copies `dmPython` / `oracledb` / `ibm_db`
from your system Python's `site-packages` into the portable Python.

Why needed: portable package only ships `pymysql`. The other drivers are
either not on PyPI (`dmPython`) or commented out in `requirements.txt`
(`oracledb`, `ibm_db`).

**Usage**:

```bat
import-db-drivers.bat ^
  C:\Users\wangds\AppData\Local\Programs\Python\Python312\Lib\site-packages ^
  D:\DataOpsStudio-portable-20260526
```

The script does NOT touch your system Python — it only reads files from it.

### `DataOpsStudio-portable-hotfix-tzdata-20260526.zip`
**473 KB · hotfix · git `978a382`**

修原 portable 包启动崩:
- `ModuleNotFoundError: No module named 'tzdata'`
- `ZoneInfoNotFoundError: 'Asia/Shanghai'`

根因:Python 在 Windows 上没自带 zoneinfo 数据,APScheduler 初始化时找
不到时区。打包时 requirements.txt 未列 `tzdata`,跨平台 pip download
没拉。

**应用方式**:双击 `apply.bat`,自动 robocopy `tzdata/` 到现场 portable
包的 `python\Lib\site-packages\`。已自动应用到 18:30+ 重打的 portable
zip,无需重复应用。

### `DataOpsStudio-portable-20260526.zip`(已重打,含 tzdata)
**74 MB · portable · git `978a382`**(18:30+) / `da4f04e`(18:00 旧版,缺 tzdata)

**完全免安装、免依赖**的 Windows 部署包。

- Python 3.12.10 embeddable(便携 Python,11 MB)
- 53 个 wheels 解到 `python/Lib/site-packages/`(uvicorn / fastapi /
  sqlglot / openpyxl / pyotp / faker / pyarrow / pandas / pydantic /
  apscheduler / python-jose / httpx / bcrypt / cryptography / pymysql /
  dmpython 等)
- tzdata 2026.2(后补,18:30+ 包)
- 应用代码 + 前端 SPA build
- `start.bat` 用 `.\python\python.exe`(纯相对路径)

**用户视角**:解压 → 双击 start.bat → 浏览器开 8010。**不需要装系统 Python**。

### `DataOpsStudio-win-full-with-wheels-20260526.zip`
**62 MB · 全量 + wheels · git `da4f04e`**

`install.bat` + `start.bat` 模式 — 依赖目标机器装好 Python 3.12。

- 全量代码 + 53 个 win_amd64 wheels
- `install.bat` 创建 `.venv` + 离线装 wheels
- `upgrade.bat` 备份现有代码 + 覆盖
- 适合"机器已有 Python,缺新依赖"场景

> 以下两个无 wheels 的"轻量"包已于 2026-05-26 18:35 从 dist/ 移除
> (用户 `.venv` 缺新依赖,启动必报 ModuleNotFoundError;已被 portable +
> with-wheels 两个包完全覆盖):
> - ~~`DataOpsStudio-win-full-upgrade-20260526.zip`~~(1.6 MB)
> - ~~`DataOpsStudio-win-incremental-20260520-to-20260526.zip`~~(2.2 MB)
>
> 如确实需要这两个轻量包(场景:目标 `.venv` 真的已齐),可从 git history
> 重打:`bash scripts/build_offline_windows.sh -v 20260526 --skip-wheels`。

---

## 2026-05-20

### `DataOpsStudio-win-incremental-0.1.0-to-20260520.zip`
**290 KB · 增量 / 无 wheels · target commit `2488581`**

从 0.1.0 全量包增量升级到 20260520 状态。

---

## 2026-05-08

### `DataOpsStudio-hotfix-2026-05-08.zip`
**992 KB · hotfix**

历史 hotfix 包(具体内容未在 BUILD_INFO 中,可能已被后续包替代)。

---

## 选包决策树

```
是否第一次部署?
├─ 是 → DataOpsStudio-portable-20260526.zip  ★★★ 推荐
│
└─ 否(已有部署)
   ├─ 想最省事,完全独立环境
   │   → DataOpsStudio-portable-20260526.zip(74 MB)
   │     解压到新目录,xcopy 把老 config/results 迁过来
   │
   ├─ 机器已装 Python 3.12,愿意跑 install.bat
   │   → DataOpsStudio-win-full-with-wheels-20260526.zip(62 MB)
   │
   └─ 18:30 前已下了 portable 但启动报 tzdata 错
       → DataOpsStudio-portable-hotfix-tzdata-20260526.zip(473 KB)
         双击 apply.bat 修复
```

## 关键 commits 时间线

| Commit | 日期 | 摘要 |
|---|---|---|
| `2488581` | 5/9 | 0520 zip 真实 baseline (Phase 11 candidates) |
| `7372bbf` | 5/21 | parquet authz e2e |
| `b8f5cdf` | 5/22 | SQL 工作台 v0.2 完整 |
| `509e5a6` | 5/24 | metadata 缓存 + 搜索 + 表详情 |
| `ea1088c` | 5/25 | SQL 模板库 |
| `698947a` | 5/25 | Explain + 4 静态规则 + 慢 SQL 阈值 |
| `7ba9cc3` | 5/26 | execution job 状态机 + timeout |
| `c2a2e8d` | 5/26 | 结果导出 4 格式 + 公式注入防御 |
| `53bbc03` | 5/26 | refresh chain 重放检测改 only-this-chain |
| `a7ce190` | 5/26 | 导出 apiDownload 修 401 |
| `da4f04e` | 5/26 | start.bat 日志 + .bat CRLF 修 + upgrade.bat 入仓 |
| `978a382` | 5/26 | requirements.txt 加 tzdata(Windows only) |

## 三方对齐状态(本机 / GitHub / 云端)

| Endpoint | Commit |
|---|---|
| 本地 main | `978a382` |
| origin/main | `978a382` |
| 云端 git HEAD | `978a382` |
| 云端 docker 镜像 | 待 rebuild(本次 tzdata 是 Windows-only fix,Linux Docker 不受影响) |
