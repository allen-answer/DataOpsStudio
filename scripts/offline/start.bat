@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM DataOps Studio - Start
REM
REM Usage:
REM   start.bat                  run in script's own directory
REM   start.bat <APP_DIR>        run in given absolute directory
REM
REM Log:
REM   logs\uvicorn-<TS>.log  (always written next to main.py)
REM ============================================================

set "APP_DIR=%~1"
if "%APP_DIR%"=="" set "APP_DIR=%~dp0"

REM Strip surrounding quotes and trailing backslash
set "APP_DIR=!APP_DIR:"=!"
if "!APP_DIR:~-1!"=="\" set "APP_DIR=!APP_DIR:~0,-1!"

cd /d "!APP_DIR!" 2>nul
if errorlevel 1 (
  echo [ERROR] Cannot cd to "!APP_DIR!"
  pause
  exit /b 1
)

if not exist main.py (
  echo [ERROR] main.py not found in "!APP_DIR!"
  echo         Pass DataOps Studio root as argument:
  echo           start.bat D:\path\to\DataOpsStudio
  pause
  exit /b 1
)

REM Pick first available Python:
REM   1. .\python\python.exe       (portable package, embeddable)
REM   2. .venv\Scripts\python.exe  (installed venv)
REM   3. python on PATH            (system Python)
set "PYEXE="
if exist python\python.exe set "PYEXE=%CD%\python\python.exe"
if not defined PYEXE if exist .venv\Scripts\python.exe set "PYEXE=%CD%\.venv\Scripts\python.exe"
if not defined PYEXE (
  where python >nul 2>nul
  if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
  echo [ERROR] No Python found. Looked for:
  echo           .\python\python.exe        ^(portable^)
  echo           .venv\Scripts\python.exe   ^(venv^)
  echo           python on PATH             ^(system^)
  pause
  exit /b 1
)

if not exist logs mkdir logs >nul 2>nul

REM Locale-agnostic timestamp via WMIC (fallback to %date%/%time%)
for /f "tokens=2 delims==" %%I in ('wmic os get LocalDateTime /value 2^>nul ^| find "="') do set "DT=%%I"
if defined DT (
  set "TS=!DT:~0,8!-!DT:~8,6!"
) else (
  set "TS=%date:~0,4%%date:~5,2%%date:~8,2%-%time:~0,2%%time:~3,2%%time:~6,2%"
  set "TS=!TS: =0!"
)
set "LOG_FILE=logs\uvicorn-!TS!.log"

echo ============================================
echo  DataOps Studio
echo  Dir    : !APP_DIR!
echo  Python : !PYEXE!
echo  URL    : http://localhost:8010
echo  Log    : !APP_DIR!\!LOG_FILE!
echo  Quit   : Ctrl+C
echo ============================================
echo.

REM Run uvicorn. cmd cannot tee natively, so pipe stdout+stderr only to
REM the log file. When uvicorn exits the script will dump the last few
REM lines back to the terminal so the user always sees the error.
"!PYEXE!" -m uvicorn main:app --host 0.0.0.0 --port 8010 > "!LOG_FILE!" 2>&1
set "EXIT_CODE=!ERRORLEVEL!"

echo.
echo ============================================
echo  uvicorn exited with code !EXIT_CODE!
echo  Full log: !APP_DIR!\!LOG_FILE!
echo --------------------------------------------
echo  Last 30 lines:
echo --------------------------------------------
REM cmd-native tail: count lines then skip
for /f %%C in ('find /v /c "" ^< "!LOG_FILE!"') do set "TOTAL=%%C"
set /a "SKIP=TOTAL-30"
if !SKIP! lss 0 set "SKIP=0"
if !SKIP! gtr 0 (
  more +!SKIP! "!LOG_FILE!"
) else (
  type "!LOG_FILE!"
)
echo --------------------------------------------

if not "!EXIT_CODE!"=="0" (
  echo.
  echo  Common causes of non-zero exit:
  echo    - Port 8010 in use:    netstat -ano ^| findstr :8010
  echo    - Missing Python deps: read the traceback above
  echo    - App import error:    read the traceback above
)
echo ============================================
pause
endlocal
