@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul

REM ============================================================
REM DataOps Studio 启动脚本(离线模式)
REM
REM 行为:
REM   - 检查 main.py / .venv / activate.bat 缺失时给明确报错 + pause
REM   - 创建 logs\ 目录(若不存在)
REM   - 用 PowerShell Tee-Object 把 uvicorn 输出同时写文件 + 显示屏幕
REM   - 日志文件:logs\uvicorn-<yyyyMMdd-HHmmss>.log
REM   - uvicorn 退出后保留窗口 pause,方便用户看错误
REM ============================================================

cd /d "%~dp0"

REM ---- sanity checks ----
if not exist main.py (
  echo [ERROR] main.py 不在当前目录:"%CD%"
  echo         start.bat 必须放在 DataOps Studio 根目录运行
  echo         请检查目录结构 ^(应含 main.py / app/ / static/ / .venv/^)
  pause
  exit /b 1
)

if not exist .venv\Scripts\activate.bat (
  echo [ERROR] 未找到 .venv\Scripts\activate.bat
  echo         请先双击 install.bat 完成 Python 虚拟环境安装
  pause
  exit /b 1
)

REM ---- 准备日志路径(用 PowerShell 拿稳定时间戳,避免 locale-sensitive %date%) ----
if not exist logs mkdir logs >nul 2>nul

for /f "delims=" %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TS=%%I"
if "%TS%"=="" set "TS=session"
set "LOG_FILE=logs\uvicorn-%TS%.log"

REM ---- 激活 venv ----
call .venv\Scripts\activate.bat
if errorlevel 1 (
  echo [ERROR] activate.bat 调用失败,venv 可能损坏
  echo         尝试删除 .venv 目录 ^+ 重新双击 install.bat
  pause
  exit /b 1
)

echo ============================================
echo  DataOps Studio
echo  访问地址: http://localhost:8010
echo  日志文件: %LOG_FILE%
echo  退出: Ctrl+C
echo ============================================
echo.

REM ---- 启动 uvicorn,同时屏幕 + 文件输出(Tee-Object) ----
REM 关键:用 PowerShell 包一层,Tee-Object 让用户能看实时日志,且全量落盘
REM 便于事后排查"启动失败但窗口闪退看不到错误"的场景。
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "& { $ErrorActionPreference='Continue'; python -m uvicorn main:app --host 0.0.0.0 --port 8010 2>&1 | Tee-Object -FilePath '%LOG_FILE%' }"

set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo ============================================
echo  uvicorn 已退出 ^(退出码=%EXIT_CODE%^)
echo  完整日志:%CD%\%LOG_FILE%
if %EXIT_CODE% NEQ 0 (
  echo.
  echo  [非正常退出] 常见原因:
  echo    - 8010 端口被占:netstat -ano ^| findstr :8010
  echo    - Python 依赖缺失:重新跑 install.bat
  echo    - main.py 语法错误:看上面日志最后几行
)
echo ============================================
pause
endlocal
