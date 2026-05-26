@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM DataOps Studio - Offline Install
REM
REM Usage:
REM   install.bat                run in script's own directory
REM   install.bat <APP_DIR>      run in given absolute directory
REM
REM Requires:
REM   - Python 3.12 on PATH (python --version works)
REM   - wheels\ directory next to this script
REM ============================================================

set "APP_DIR=%~1"
if "%APP_DIR%"=="" set "APP_DIR=%~dp0"

set "APP_DIR=!APP_DIR:"=!"
if "!APP_DIR:~-1!"=="\" set "APP_DIR=!APP_DIR:~0,-1!"

cd /d "!APP_DIR!" 2>nul
if errorlevel 1 (
  echo [ERROR] Cannot cd to "!APP_DIR!"
  pause
  exit /b 1
)

if not exist requirements.txt (
  echo [ERROR] requirements.txt not found in "!APP_DIR!"
  echo         Pass DataOps Studio root as argument:
  echo           install.bat D:\path\to\DataOpsStudio
  pause
  exit /b 1
)

if not exist wheels (
  echo [ERROR] wheels\ directory not found.
  echo         This package is "lightweight" without wheels;
  echo         use the package with bundled wheels instead.
  pause
  exit /b 1
)

echo ============================================
echo  DataOps Studio - Offline Install
echo  Dir : !APP_DIR!
echo ============================================
echo.

REM 1. Check Python
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found on PATH.
  echo         Install Python 3.12 from:
  echo           https://www.python.org/downloads/release/python-3120/
  echo         Check "Add python.exe to PATH" during install.
  pause
  exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo Python: !PYVER!
echo.

REM 2. Create .venv if missing
if exist .venv (
  echo .venv exists, skip create.
) else (
  echo Creating virtual environment .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] venv creation failed.
    pause
    exit /b 1
  )
)

REM 3. Install wheels into .venv (offline)
echo.
echo Installing wheels into .venv ^(offline^) ...
call .venv\Scripts\activate.bat
if errorlevel 1 (
  echo [ERROR] activate.bat failed, .venv may be broken.
  pause
  exit /b 1
)

python -m pip install --upgrade pip --no-index --find-links=wheels
python -m pip install --no-index --find-links=wheels -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed.
  echo         The wheels\ folder may be incomplete, or the target
  echo         Python version differs from the build environment ^(3.12^).
  pause
  exit /b 1
)

REM 4. Initialize default config files (only if not present)
if not exist config\datasources.json if exist config\datasources.example.json (
  copy /Y config\datasources.example.json config\datasources.json >nul
  echo Init: config\datasources.json
)
if not exist config\tasks.json if exist config\tasks.example.json (
  copy /Y config\tasks.example.json config\tasks.json >nul
  echo Init: config\tasks.json
)

echo.
echo ============================================
echo  Install complete.
echo  Next: run start.bat
echo ============================================
pause
endlocal
