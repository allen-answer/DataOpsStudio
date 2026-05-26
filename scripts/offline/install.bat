@echo off
setlocal
chcp 65001 > nul

REM DataOps Studio 离线安装脚本
REM 在目标机器上执行：创建 venv，装 wheels 目录里的离线依赖
REM 前置：目标机器装好 Python 3.12（python --version 能用）

cd /d "%~dp0"

echo ============================================
echo  DataOps Studio - Offline Install
echo ============================================
echo.

REM 1. 检查 Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未检测到 python，请先安装 Python 3.12
    echo         https://www.python.org/downloads/release/python-3120/
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Python: %PYVER%
echo.

REM 2. 创建 venv（已存在则跳过）
if exist .venv (
    echo .venv 已存在，跳过创建
) else (
    echo 创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] venv 创建失败
        pause
        exit /b 1
    )
)

REM 3. 离线装依赖
echo.
echo 离线安装依赖（不联网）...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip --no-index --find-links=wheels
python -m pip install --no-index --find-links=wheels -r requirements.txt
if errorlevel 1 (
    echo [ERROR] 依赖安装失败 —— wheels 目录可能不完整
    echo         请检查打包机器和目标机器的 Python 版本是否一致（3.12）
    pause
    exit /b 1
)

REM 4. 配置初始化（首次部署）
if not exist config\datasources.json (
    if exist config\datasources.example.json (
        copy /Y config\datasources.example.json config\datasources.json >nul
        echo 初始化 config\datasources.json
    )
)
if not exist config\tasks.json (
    if exist config\tasks.example.json (
        copy /Y config\tasks.example.json config\tasks.json >nul
        echo 初始化 config\tasks.json
    )
)

echo.
echo ============================================
echo  安装完成。下一步：双击 start.bat 启动服务
echo ============================================
pause
endlocal
