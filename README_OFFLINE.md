# DataOps Studio - 离线部署说明

适用于客户离线现场、不能联网装包的环境。

> 这份文档同时存在于仓库根（开发者参考）和 release zip 解压根目录（目标机器查阅）。

## 包内结构

解压 `DataOpsStudio-win-offline-{version}.zip` 后看到：

```
DataOpsStudio-win-offline-{version}/
├── app/                       # 后端源码
├── main.py                    # FastAPI 入口
├── requirements.txt
├── static/
│   └── spa/                   # 前端构建产物（已 build 好）
├── wheels/                    # Python 离线依赖
├── config/                    # 示例配置（*.example.json）
├── init_db/                   # MySQL 初始化 SQL（可选）
├── install.bat                # 离线安装入口
├── start.bat                  # 启动入口
├── README_OFFLINE.md          # 本文件
└── BUILD_INFO.json            # 版本和构建时间
```

## 前置要求

目标机器只需要：

- **Windows 10 / 11** (x64)
- **Python 3.12**（[官方下载页](https://www.python.org/downloads/release/python-3120/)，安装时勾选「Add Python to PATH」）
- 一个支持的数据库（MySQL / Oracle / DM / DB2 任选）—— 可选，仅做血缘分析或 Excel 对比时不需要

> **Python 版本必须和打包机器一致**（3.12），否则 wheels 目录里的二进制扩展（如 `pymysql` 的 cryptography 依赖）会装不上。

## 安装步骤

### 1. 解压

把 zip 解压到一个**全英文路径**目录，避免中文 / 空格目录引发 wheel 解析问题。例：

```
D:\DataOpsStudio\
```

### 2. 装依赖

双击 `install.bat`：

- 自动创建 `.venv`（虚拟环境）
- 从 `wheels/` 目录离线安装 `requirements.txt` 列出的所有 Python 包
- 把 `config/*.example.json` 拷成首次运行的 `config/*.json`

如果出现 `Could not find a version that satisfies the requirement ...` 错误：
- 通常是打包机器和目标机器的 Python 版本不一致
- 或者 `wheels/` 没拷全 —— 重新解压一次

### 3. 启动

双击 `start.bat`：

- 激活 venv
- 启动 uvicorn 监听 `0.0.0.0:8010`

浏览器打开：

```
http://localhost:8010
```

> 如果机器有别的服务占用 8010，编辑 `start.bat` 把 `--port 8010` 改成其他端口。

## 数据库驱动按需准备

`requirements.txt` 默认只带 **MySQL 驱动**（`pymysql` + `cryptography`）。其他数据库需要单独追加 wheel：

| 数据库 | 包名 | 备注 |
|--------|------|------|
| MySQL | `pymysql`、`cryptography` | 已默认包含 |
| Oracle | `oracledb` | thin 模式 |
| DM（达梦） | `dmPython` | 厂商发布的 wheel |
| DB2 | `ibm_db`、`ibm_db_dbi` | 需要 IBM CLI driver；详见 IBM 文档 |

补充驱动的方式（在打包机器上）：

```powershell
pip download dmPython oracledb -d wheels --platform win_amd64 --python-version 3.12 --only-binary=:all:
```

把额外的 wheel 拷进 release zip 的 `wheels/` 目录，目标机器在装好基础依赖后：

```
.venv\Scripts\activate
pip install --no-index --find-links=wheels dmPython oracledb
```

## 数据持久化

应用所有运行时状态（数据源、对比任务、作业流定义、历史结果）都落到本地 JSON 和文件，不需要外部数据库：

```
config/datasources.json         # 数据源配置
config/tasks.json               # 对比任务配置
config/workflows.json           # 作业流配置
config/jobs.json                # 异步任务状态
results/                        # 每次运行的 JSON / Excel 结果
results/workflow_runs/          # 作业流 run 历史 + 导出文件
logs/                           # 应用日志
```

**升级版本**时只需要保留 `config/` 和 `results/` 两个目录，覆盖其余文件即可。

## 常见问题

**Q: install.bat 卡在 `Installing collected packages` 不动**
A: 大概率在装 `cryptography` —— 这是 wheel 体积最大的依赖（~6 MB）。等 1-2 分钟。

**Q: start.bat 报 `ModuleNotFoundError: No module named 'fastapi'`**
A: 没装依赖或 venv 损坏。删掉 `.venv` 目录，重新双击 `install.bat`。

**Q: 浏览器打开 8010 端口空白页**
A: 检查 `static/spa/index.html` 是否存在 —— 离线包默认带前端构建产物，缺失说明 zip 没解压完整。

**Q: 想用其他端口**
A: 编辑 `start.bat`，把 `--port 8010` 改成你要的端口；同时检查防火墙规则。

**Q: 想在内网多人访问**
A: `start.bat` 里 `--host 0.0.0.0` 已经允许内网访问；其他人浏览器打开 `http://本机IP:8010`。

**Q: 配置导出 / 导入**
A: 应用 UI 里有「导出配置」按钮（产 JSON），目标机器「导入配置」回填即可。

## 升级 / 重新打包

打包机器（开发者）：

```powershell
.\scripts\build_offline_windows.ps1 -Version 0.2.0
```

参数：

- `-Version` 版本号（默认 `dev`）
- `-SkipFrontend` 跳过 npm build（前端没改时省时间）
- `-SkipWheels` 跳过 pip download（依赖没变时省时间）

输出 `DataOpsStudio-win-offline-0.2.0.zip` 在仓库根。

## 联系 / 反馈

详见仓库根 `README.md`。
