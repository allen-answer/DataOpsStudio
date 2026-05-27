# 打 Windows Portable 自包含包

`scripts/build_portable_windows.ps1` 一键打出 `DataOpsStudio-portable-<version>.zip`,目标机器**零依赖**(不用装 Python,不用 import-db-drivers)。

跟 `build_offline_windows.ps1` 的区别:
- 后者:目标机器自己装 Python 3.12,install.bat 离线 pip 装 wheel
- 本脚本:python\ 已经是完整 site-packages,**用户解压双击 start.bat 就跑**

---

## 一、打包机要求

| 项 | 版本 / 说明 |
|---|---|
| OS | Windows 10/11 64 位(或 Windows Server 2019+) |
| PowerShell | 5+(Windows 自带) |
| Node.js | 20+(npm build 前端用;`-SkipFrontend` 可跳) |
| 网络 | 能访问 `python.org` + `bootstrap.pypa.io` + `pypi.org`(`-PyEmbedZip` + 本地 wheels 可离线) |
| 磁盘 | 1GB 空闲(staging + zip) |

---

## 二、准备 VendorWheels(可选,但强烈推荐)

PyPI 上的 wheel 直接 pip 装(`pymysql` / `oracledb` 都有上 PyPI),但**商业驱动**可能没上 PyPI 或版本绑特定 Python 版本:

- **dmPython**:达梦官方分发,不在 PyPI。需要从达梦安装目录或官网下载 `.whl`
- **ibm_db**:PyPI 有但绑特定 Python 版本,需要 `cp312-win_amd64` 那个

### 推荐做法

```powershell
# 1. 任意建个目录
mkdir C:\dataops-vendor-wheels

# 2. 把所有需要的 .whl 放进去,例如:
#    dmPython-2.5.4-cp312-cp312-win_amd64.whl
#    oracledb-2.4.1-cp312-cp312-win_amd64.whl  (这个 PyPI 也有,本地放也行)
#    ibm_db-3.2.4-cp312-cp312-win_amd64.whl

# 3. 检查
Get-ChildItem C:\dataops-vendor-wheels -Filter "*.whl"
```

### 怎么找这些 wheel

| 驱动 | 来源 |
|---|---|
| `dmPython` | 达梦数据库官方下载页(随产品分发),解压后在 drivers\python\ 找 |
| `oracledb` | PyPI 直接装 `pip download oracledb --platform win_amd64 --python-version 3.12 --only-binary=:all:` |
| `ibm_db` | PyPI 同上;装好需要 `clidriver\` 子目录,build 脚本会从 site-packages 自动带 |
| `pymysql` | PyPI 纯 Python,任何环境都装 OK,不需要本地 wheel |

如果你有一台已经装好这 4 个驱动的机器,可以直接从 `python\Lib\site-packages\` 取(或者 pip wheel 命令导出):

```powershell
# 在装好驱动的机器上
pip wheel -w C:\dataops-vendor-wheels dmPython oracledb ibm_db
```

---

## 三、运行打包

```powershell
# 最简(网络要通)
.\scripts\build_portable_windows.ps1 -Version 20260527 -VendorWheels C:\dataops-vendor-wheels

# 跳前端构建(已经 npm run build 过 static\spa\)
.\scripts\build_portable_windows.ps1 -Version 20260527 -VendorWheels C:\dataops-vendor-wheels -SkipFrontend

# 完全离线 build(python embeddable 已经下好,pip 也能离线 — 需要 wheel 全准备齐)
.\scripts\build_portable_windows.ps1 `
    -Version 20260527 `
    -VendorWheels C:\dataops-vendor-wheels `
    -PyEmbedZip C:\Downloads\python-3.12.7-embed-amd64.zip
```

参数表:

| 参数 | 默认 | 说明 |
|---|---|---|
| `-Version` | `dev` | 版本号,影响 zip 名 `DataOpsStudio-portable-<version>.zip` |
| `-VendorWheels` | 空 | 含 .whl 的目录;不指定则 DM/Oracle/DB2 缺失 |
| `-PyEmbedZip` | 自动下载 | 本地 cache 的 python embeddable zip(`python-3.12.7-embed-amd64.zip`) |
| `-PythonVersion` | `3.12.7` | embeddable 的 Python 版本 |
| `-SkipFrontend` | 否 | 跳过 npm build(已 build 过) |
| `-SkipPip` | 否 | dev 用,跳过 pip install(zip 跑不起来,但 staging 可看结构) |
| `-OutputDir` | `.` | zip 输出目录 |

---

## 四、产物结构

```
DataOpsStudio-portable-20260527/
├── python\                 ★ Python 3.12 embeddable + 所有 wheel
│   ├── python.exe
│   ├── python312._pth      已启用 site-packages
│   └── Lib\site-packages\
│       ├── pymysql\
│       ├── dmPython\
│       ├── oracledb\
│       ├── ibm_db.pyd
│       ├── ibm_db_dbi.py
│       ├── clidriver\      DB2 native lib
│       └── (其它 requirements.txt 依赖)
├── app\                    后端代码
├── main.py
├── static\spa\             前端构建产物
├── config\                 example 配置
├── data\                   空 SQLite 目录
├── logs\                   空
├── results\                空
├── start.bat               ★ v2 启动脚本(含 .env 加载)
├── update.bat              代码增量升级
├── rollback.bat
├── enable-prod.bat         一键切 prod
├── disable-prod.bat
├── import-db-drivers.bat   备用 — 老 portable 跨平台复制驱动
├── docker-compose.yml      给 Docker 部署用
├── .env.example
├── BUILD_INFO.json
└── requirements.txt
```

预期大小 70-100 MB(取决于驱动 wheel 大小)。

---

## 五、验证产物

打包脚本会自动验证驱动可 import:

```
  4c. 验证驱动:
       [ok] pymysql
       [ok] dmPython
       [ok] oracledb
       [ok] ibm_db
```

如果有 `[missing]`,说明对应 wheel 没装上(版本不匹配 / VendorWheels 没放)。

手动二次验证:

```powershell
# 解压到临时目录
Expand-Archive .\dist\DataOpsStudio-portable-20260527.zip -DestinationPath C:\Temp\dataops-test

# 切到目录
cd C:\Temp\dataops-test\DataOpsStudio-portable-20260527

# 测 import
.\python\python.exe -c "import pymysql, dmPython, oracledb, ibm_db; print('all OK')"

# 启动跑一会儿
.\start.bat
# 浏览器开 http://localhost:8010/api/drivers,看 4 个驱动是不是都 available
```

---

## 六、CI 集成(可选)

`.github\workflows\release.yml` 已经有 Windows runner 打包 release。可以加一个 job 跑本脚本:

```yaml
jobs:
  build-portable-win:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - name: Restore vendor wheels cache
        uses: actions/cache@v4
        with:
          path: vendor-wheels
          key: vendor-wheels-${{ hashFiles('vendor-wheels/manifest.txt') }}
      - name: Build portable
        run: |
          .\scripts\build_portable_windows.ps1 `
            -Version ${{ github.ref_name }} `
            -VendorWheels vendor-wheels
      - uses: actions/upload-artifact@v4
        with:
          name: portable-zip
          path: dist\DataOpsStudio-portable-*.zip
```

注意 GitHub Actions windows-latest 默认没装达梦 dmPython wheel — 需要把 vendor wheel 通过 GitHub Releases / cache / S3 提前放好。

---

## 七、常见坑

### Q: 跑脚本后某个驱动 [missing]

- 检查 `VendorWheels` 目录里 .whl 文件名是否含 `cp312-cp312-win_amd64`(版本必须匹配 PythonVersion + 架构)
- 看脚本输出的 Warning 行 — pip 装失败的具体原因

### Q: ibm_db 装上了但 import 时 DLL load failed

- `ibm_db` wheel 自带 `clidriver\bin\` 下的 DLL,但默认不在 PATH。`app\dbclients\drivers.py::add_db2_dll_directories()` 会自动加到 PATH + add_dll_directory,这个逻辑在打包后启动时跑,自动处理

### Q: Python 版本不一致警告

- `PythonVersion` 参数决定 embeddable 版本。VendorWheels 里的 .whl 必须跟它的 cp 版本匹配(cp312 / cp311 / cp310...)
- 升级 PythonVersion 时,所有 vendor wheel 都要重新下载对应版本

### Q: PowerShell 报 "执行策略" 错误

- 一次性放行当前会话:`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
- 然后再跑 build 脚本
