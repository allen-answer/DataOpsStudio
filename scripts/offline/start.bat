@echo off
setlocal

REM DataOps Studio 启动脚本（离线模式）
REM 直接运行 uvicorn，监听 8010；浏览器打开 http://localhost:8010

cd /d "%~dp0"

if not exist .venv\Scripts\activate.bat (
    echo [ERROR] 没找到 .venv，请先双击 install.bat 完成安装
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

echo ============================================
echo  DataOps Studio
echo  http://localhost:8010
echo  Ctrl+C 退出
echo ============================================
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8010

endlocal
