# Windows 离线 release 打包脚本
#
# 输出 DataOpsStudio-win-offline-{version}.zip，包内包含：
#   app/                   后端源码
#   main.py                FastAPI 入口
#   requirements.txt
#   static/spa/            前端构建产物（脚本里 npm build 出来）
#   wheels/                Python 离线依赖（pip download）
#   config/*.example.json  示例配置
#   init_db/               MySQL 初始化 SQL（可选 — 给装 MySQL 的客户用）
#   install.bat            离线安装入口
#   start.bat              启动入口
#   README_OFFLINE.md      离线模式说明
#
# 用法：
#   .\scripts\build_offline_windows.ps1
#   .\scripts\build_offline_windows.ps1 -Version 0.2.0
#   .\scripts\build_offline_windows.ps1 -Version 0.2.0 -SkipFrontend
#
# 环境要求（仅打包机器，不影响目标机器）：
#   - Python 3.12（与目标机器一致）
#   - Node.js 20+（除非 -SkipFrontend）
#
# 目标机器只需要：Python 3.12。

[CmdletBinding()]
param(
    [string]$Version = "dev",
    [switch]$SkipFrontend,
    [switch]$SkipWheels,
    [string]$OutputDir = "."
)

$ErrorActionPreference = "Stop"

# 切到仓库根（脚本所在目录的上一级）
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "=== DataOps Studio 离线 release 打包 ===" -ForegroundColor Cyan
Write-Host "仓库根：$RepoRoot"
Write-Host "版本号：$Version"
Write-Host ""

$StagingName = "DataOpsStudio-win-offline-$Version"
$StagingDir = Join-Path $env:TEMP $StagingName
$ZipPath = Join-Path (Resolve-Path $OutputDir) "$StagingName.zip"

if (Test-Path $StagingDir) {
    Write-Host "清理旧 staging 目录：$StagingDir"
    Remove-Item -Recurse -Force $StagingDir
}
New-Item -ItemType Directory -Path $StagingDir | Out-Null

# ---- 1. 前端构建 ----
if (-not $SkipFrontend) {
    Write-Host "[1/5] 构建前端..." -ForegroundColor Yellow
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
    Write-Host "[1/5] 跳过前端构建（-SkipFrontend）" -ForegroundColor DarkGray
}

if (-not (Test-Path "static\spa\index.html")) {
    throw "static/spa/index.html 不存在，前端 build 失败或被跳过；离线包不可用"
}

# ---- 2. 下载 Python wheels ----
if (-not $SkipWheels) {
    Write-Host "[2/5] 下载 Python wheels..." -ForegroundColor Yellow
    $WheelsTmp = Join-Path $StagingDir "wheels"
    New-Item -ItemType Directory -Path $WheelsTmp | Out-Null
    pip download -r requirements.txt -d $WheelsTmp --platform win_amd64 --python-version 3.12 --only-binary=:all:
    if ($LASTEXITCODE -ne 0) {
        # 退化策略：有些包没有纯 wheel —— 拿源码，目标机器有 build tools 就能装
        Write-Host "  纯 wheel 下载失败，退到源码 + wheel 混合模式" -ForegroundColor DarkYellow
        Remove-Item -Recurse -Force $WheelsTmp
        New-Item -ItemType Directory -Path $WheelsTmp | Out-Null
        pip download -r requirements.txt -d $WheelsTmp
        if ($LASTEXITCODE -ne 0) { throw "pip download 失败" }
    }
} else {
    Write-Host "[2/5] 跳过 wheels 下载（-SkipWheels）" -ForegroundColor DarkGray
    New-Item -ItemType Directory -Path (Join-Path $StagingDir "wheels") | Out-Null
}

# ---- 3. 拷源码到 staging ----
Write-Host "[3/5] 拷贝源码到 staging..." -ForegroundColor Yellow

$ItemsToCopy = @(
    @{ Source = "app"; Dest = "app"; Type = "Directory" }
    @{ Source = "static\spa"; Dest = "static\spa"; Type = "Directory" }
    @{ Source = "init_db"; Dest = "init_db"; Type = "Directory" }
    @{ Source = "main.py"; Dest = "main.py"; Type = "File" }
    @{ Source = "requirements.txt"; Dest = "requirements.txt"; Type = "File" }
    @{ Source = "scripts\offline\install.bat"; Dest = "install.bat"; Type = "File" }
    @{ Source = "scripts\offline\start.bat"; Dest = "start.bat"; Type = "File" }
    @{ Source = "README_OFFLINE.md"; Dest = "README_OFFLINE.md"; Type = "File" }
)

foreach ($item in $ItemsToCopy) {
    $sourcePath = Join-Path $RepoRoot $item.Source
    $destPath = Join-Path $StagingDir $item.Dest
    if (-not (Test-Path $sourcePath)) {
        throw "缺少必备文件：$sourcePath"
    }
    if ($item.Type -eq "Directory") {
        Copy-Item -Recurse -Force $sourcePath $destPath
    } else {
        $destDir = Split-Path -Parent $destPath
        if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir | Out-Null }
        Copy-Item -Force $sourcePath $destPath
    }
}

# config 只带 example，不带真实配置
$ConfigDest = Join-Path $StagingDir "config"
New-Item -ItemType Directory -Path $ConfigDest | Out-Null
Get-ChildItem -Path "config" -Filter "*.example.json" | ForEach-Object {
    Copy-Item -Force $_.FullName (Join-Path $ConfigDest $_.Name)
}

# 清理 __pycache__ 和测试缓存
Get-ChildItem -Recurse -Path $StagingDir -Filter "__pycache__" -Directory -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# ---- 4. 写版本文件（方便目标机器排错）----
Write-Host "[4/5] 写版本元数据..." -ForegroundColor Yellow
$BuildInfo = @{
    version = $Version
    built_at = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    git_commit = (git rev-parse --short HEAD 2>$null)
    python_target = "3.12 / win_amd64"
} | ConvertTo-Json
Set-Content -Path (Join-Path $StagingDir "BUILD_INFO.json") -Value $BuildInfo -Encoding utf8

# ---- 5. 打 zip ----
Write-Host "[5/5] 打 zip：$ZipPath" -ForegroundColor Yellow
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path "$StagingDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal

# ---- 收尾 ----
$ZipSize = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host ""
Write-Host "=== 完成 ===" -ForegroundColor Green
Write-Host "Release 包：$ZipPath ($ZipSize MB)"
Write-Host "Staging：  $StagingDir（可手工查内容；下次重 build 会清掉）"
Write-Host ""
Write-Host "目标机器使用步骤："
Write-Host "  1. 解压 zip"
Write-Host "  2. 双击 install.bat（创建 venv，离线装 wheels）"
Write-Host "  3. 双击 start.bat（启动 uvicorn，监听 8010）"
Write-Host "  4. 浏览器打开 http://localhost:8010"
