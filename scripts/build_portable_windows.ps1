# Windows 自包含 portable 打包脚本
#
# 输出 DataOpsStudio-portable-<version>.zip,包内包含:
#   python\                   Python 3.12 embeddable + 所有 wheel 已 pip install
#                              (含 pymysql / dmPython / oracledb / ibm_db)
#   app\                      后端源码
#   main.py                   FastAPI 入口
#   requirements.txt          依赖清单(参考用,wheels 都已装好)
#   static\spa\               前端构建产物
#   config\*.example.json     示例配置
#   init_db\                  MySQL 初始化 SQL
#   start.bat                 启动脚本(v2 加 .env 加载)
#   update.bat                增量代码升级
#   rollback.bat              代码回滚
#   enable-prod.bat           一键切 prod
#   disable-prod.bat          一键退 dev
#   import-db-drivers.bat     备用 — 老 portable 跨平台复制驱动
#   BUILD_INFO.json           版本元数据
#   README_OFFLINE.md         离线模式说明
#
# 跟 build_offline_windows.ps1 的差异:
#   - 后者:目标机器自己装 Python,install.bat 离线 pip 装 wheel
#   - 本脚本:python\ 已经是完整 site-packages,用户解压即跑,不需要装 Python
#
# 用法:
#   .\scripts\build_portable_windows.ps1 -Version 20260527
#   .\scripts\build_portable_windows.ps1 -Version 20260527 -VendorWheels C:\wheels
#   .\scripts\build_portable_windows.ps1 -Version 20260527 -SkipFrontend
#
# 环境要求(仅打包机器):
#   - Windows + PowerShell 5+
#   - Node.js 20+(除非 -SkipFrontend)
#   - 网络可访问 https://www.python.org / https://bootstrap.pypa.io
#     (除非 -PyEmbedZip 指定本地 cache)
#   - VendorWheels 目录建议含:
#       dmPython-*-cp312-cp312-win_amd64.whl
#       oracledb-*-cp312-cp312-win_amd64.whl
#       ibm_db-*-cp312-cp312-win_amd64.whl
#       (pymysql 走 PyPI,不强制本地 wheel)
#
# 目标机器要求:无(零依赖)。

[CmdletBinding()]
param(
    [string]$Version = "dev",
    [string]$VendorWheels = "",
    [string]$PyEmbedZip = "",
    [string]$PythonVersion = "3.12.7",
    [switch]$SkipFrontend,
    [switch]$SkipPip,
    [string]$OutputDir = "."
)

$ErrorActionPreference = "Stop"

# ---- 切到仓库根 ----
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "=== DataOps Studio Portable 打包(自包含 Python+wheels) ===" -ForegroundColor Cyan
Write-Host "仓库根 : $RepoRoot"
Write-Host "版本号 : $Version"
Write-Host ""

$StagingName = "DataOpsStudio-portable-$Version"
$StagingDir = Join-Path $env:TEMP $StagingName
$ZipPath = Join-Path (Resolve-Path $OutputDir) "$StagingName.zip"

if (Test-Path $StagingDir) {
    Write-Host "清理旧 staging:$StagingDir"
    Remove-Item -Recurse -Force $StagingDir
}
New-Item -ItemType Directory -Path $StagingDir | Out-Null

# ---- 1. 前端构建 ----
if (-not $SkipFrontend) {
    Write-Host "[1/7] 构建前端..." -ForegroundColor Yellow
    Push-Location frontend\frontend
    try {
        if (-not (Test-Path node_modules)) {
            npm ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "npm ci 失败" }
        }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build 失败" }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[1/7] 跳过前端构建(-SkipFrontend)" -ForegroundColor DarkGray
}
if (-not (Test-Path "static\spa\index.html")) {
    throw "static/spa/index.html 不存在 — 前端构建失败或被跳过"
}

# ---- 2. 准备 Python embeddable ----
Write-Host "[2/7] 准备 Python embeddable..." -ForegroundColor Yellow
$PyDir = Join-Path $StagingDir "python"
New-Item -ItemType Directory -Path $PyDir | Out-Null

if (-not $PyEmbedZip) {
    # 默认从 python.org 下载
    $PyEmbedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    $PyEmbedZip = Join-Path $env:TEMP "python-$PythonVersion-embed-amd64.zip"
    if (-not (Test-Path $PyEmbedZip)) {
        Write-Host "  下载: $PyEmbedUrl"
        Invoke-WebRequest -Uri $PyEmbedUrl -OutFile $PyEmbedZip
    } else {
        Write-Host "  使用 cache: $PyEmbedZip"
    }
}
Expand-Archive -Path $PyEmbedZip -DestinationPath $PyDir -Force
Write-Host "  Python embeddable 已解压到 $PyDir"

# ---- 3. 启用 site-packages (改 ._pth) ----
Write-Host "[3/7] 启用 site-packages..." -ForegroundColor Yellow
$PthFile = Get-ChildItem -Path $PyDir -Filter "python*._pth" | Select-Object -First 1
if (-not $PthFile) {
    throw "未找到 python*._pth — embeddable 包结构异常"
}
# embeddable 默认有 `#import site` 行被注释 — 取消注释,启用 site-packages 自动加载
$PthContent = Get-Content $PthFile.FullName
$PthContent = $PthContent | ForEach-Object { $_ -replace "^#import site", "import site" }
# 加 Lib/site-packages 进 sys.path(embeddable 默认没这条)
if ($PthContent -notmatch "Lib\\site-packages") {
    $PthContent += "Lib\site-packages"
}
Set-Content -Path $PthFile.FullName -Value $PthContent -Encoding ASCII
New-Item -ItemType Directory -Path (Join-Path $PyDir "Lib\site-packages") -Force | Out-Null
Write-Host "  ._pth 已启用 site, site-packages 路径已加"

# ---- 4. 引导 pip + pip install 所有 wheel ----
if (-not $SkipPip) {
    Write-Host "[4/7] 引导 pip 并安装依赖..." -ForegroundColor Yellow
    $PyExe = Join-Path $PyDir "python.exe"
    $GetPip = Join-Path $env:TEMP "get-pip.py"
    if (-not (Test-Path $GetPip)) {
        Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPip
    }
    & $PyExe $GetPip --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw "pip 引导失败" }

    $SitePackages = Join-Path $PyDir "Lib\site-packages"

    # 4a. requirements.txt(主要依赖,从 PyPI / 已 cache wheel)
    Write-Host "  4a. pip install -r requirements.txt"
    & $PyExe -m pip install --target $SitePackages --no-warn-script-location -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "requirements 安装失败" }

    # 4b. vendor wheels(DM / Oracle / DB2 等 PyPI 缺失或商业 wheel)
    if ($VendorWheels -and (Test-Path $VendorWheels)) {
        $VendorFiles = Get-ChildItem -Path $VendorWheels -Filter "*.whl"
        if ($VendorFiles) {
            Write-Host "  4b. pip install vendor wheels ($($VendorFiles.Count) 个)"
            foreach ($wheel in $VendorFiles) {
                Write-Host "       $($wheel.Name)"
                & $PyExe -m pip install --target $SitePackages --no-warn-script-location --no-deps $wheel.FullName
                if ($LASTEXITCODE -ne 0) {
                    Write-Warning "vendor wheel 安装失败: $($wheel.Name) — 跳过(可能版本不匹配)"
                }
            }
        } else {
            Write-Host "  4b. VendorWheels 目录为空 — 跳过" -ForegroundColor DarkYellow
        }
    } else {
        Write-Host "  4b. 未指定 -VendorWheels — DM / Oracle / DB2 驱动将缺失" -ForegroundColor DarkYellow
        Write-Host "       用户拿到 portable 后需要自行运行 import-db-drivers.bat"
    }

    # 4c. 验证关键驱动
    Write-Host "  4c. 验证驱动:"
    foreach ($mod in @("pymysql", "dmPython", "oracledb", "ibm_db")) {
        & $PyExe -c "import $mod" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "       [ok] $mod" -ForegroundColor Green
        } else {
            Write-Host "       [missing] $mod" -ForegroundColor DarkYellow
        }
    }
} else {
    Write-Host "[4/7] 跳过 pip(-SkipPip,仅 dev)" -ForegroundColor DarkGray
}

# ---- 5. 拷源码 + 脚本到 staging ----
Write-Host "[5/7] 拷贝源码 + 脚本到 staging..." -ForegroundColor Yellow
$ItemsToCopy = @(
    @{ Source = "app";                                 Dest = "app";                       Type = "Directory" }
    @{ Source = "static\spa";                          Dest = "static\spa";                Type = "Directory" }
    @{ Source = "init_db";                             Dest = "init_db";                   Type = "Directory" }
    @{ Source = "main.py";                             Dest = "main.py";                   Type = "File" }
    @{ Source = "requirements.txt";                    Dest = "requirements.txt";          Type = "File" }
    @{ Source = "docker-compose.yml";                  Dest = "docker-compose.yml";        Type = "File" }
    @{ Source = ".env.example";                        Dest = ".env.example";              Type = "File" }
    @{ Source = "scripts\offline\start.bat";           Dest = "start.bat";                 Type = "File" }
    @{ Source = "scripts\offline\update.bat";          Dest = "update.bat";                Type = "File" }
    @{ Source = "scripts\offline\rollback.bat";        Dest = "rollback.bat";              Type = "File" }
    @{ Source = "scripts\offline\enable-prod.bat";     Dest = "enable-prod.bat";           Type = "File" }
    @{ Source = "scripts\offline\disable-prod.bat";    Dest = "disable-prod.bat";          Type = "File" }
    @{ Source = "scripts\offline\import-db-drivers.bat"; Dest = "import-db-drivers.bat";   Type = "File" }
)

foreach ($item in $ItemsToCopy) {
    $sourcePath = Join-Path $RepoRoot $item.Source
    $destPath = Join-Path $StagingDir $item.Dest
    if (-not (Test-Path $sourcePath)) {
        Write-Warning "跳过缺失文件: $($item.Source)"
        continue
    }
    if ($item.Type -eq "Directory") {
        Copy-Item -Recurse -Force $sourcePath $destPath
    } else {
        $destDir = Split-Path -Parent $destPath
        if ($destDir -and -not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir | Out-Null }
        Copy-Item -Force $sourcePath $destPath
    }
}

# config\ 只带 example,不带真实配置
$ConfigDest = Join-Path $StagingDir "config"
New-Item -ItemType Directory -Path $ConfigDest -Force | Out-Null
Get-ChildItem -Path "config" -Filter "*.example.*" | ForEach-Object {
    Copy-Item -Force $_.FullName (Join-Path $ConfigDest $_.Name)
}

# logs / results / data 空目录预创建,避免 ensure_dirs 启动时 mkdir 权限问题
foreach ($d in @("logs", "results", "data")) {
    New-Item -ItemType Directory -Path (Join-Path $StagingDir $d) -Force | Out-Null
    # 放一个 .gitkeep 让空目录跟着 zip
    Set-Content -Path (Join-Path $StagingDir "$d\.gitkeep") -Value "" -Encoding ASCII
}

# 清理 __pycache__
Get-ChildItem -Recurse -Path $StagingDir -Filter "__pycache__" -Directory -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# ---- 6. 写 BUILD_INFO.json ----
Write-Host "[6/7] 写版本元数据..." -ForegroundColor Yellow
$GitCommit = ""
try { $GitCommit = (git rev-parse --short HEAD).Trim() } catch {}
$BuildInfo = [PSCustomObject]@{
    version       = "$Version-portable"
    built_at      = (Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz")
    git_commit    = $GitCommit
    python_target = "$PythonVersion / win_amd64"
    built_on      = "$([System.Environment]::OSVersion.VersionString) / $env:PROCESSOR_ARCHITECTURE"
    kind          = "portable-full"
} | ConvertTo-Json
Set-Content -Path (Join-Path $StagingDir "BUILD_INFO.json") -Value $BuildInfo -Encoding utf8

# ---- 7. 打 zip ----
Write-Host "[7/7] 打 zip:$ZipPath" -ForegroundColor Yellow
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path "$StagingDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal

# ---- 收尾 ----
$ZipSize = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host ""
Write-Host "=== 完成 ===" -ForegroundColor Green
Write-Host "Portable 包 : $ZipPath ($ZipSize MB)"
Write-Host "Staging     : $StagingDir(可手工查内容;下次重 build 会清掉)"
Write-Host ""
Write-Host "目标机器使用步骤:"
Write-Host "  1. 解压 zip 到任意目录(D:\DataOpsStudio)"
Write-Host "  2. 双击 start.bat"
Write-Host "  3. 浏览器 http://localhost:8010(admin/admin)"
Write-Host ""
Write-Host "切 prod 模式:解压后双击 enable-prod.bat"
