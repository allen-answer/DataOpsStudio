@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM DataOps Studio - Disable Production Mode
REM
REM What this does:
REM   Renames .env to .env.disabled in the install dir, so on next
REM   restart start.bat won't load it and the app falls back to dev mode.
REM
REM This does NOT delete the file - your JWT secret is preserved in
REM .env.disabled and you can rename it back any time.
REM
REM Usage:
REM   disable-prod.bat                 (current dir = install root)
REM   disable-prod.bat D:\DataOpsStudio
REM ============================================================

set "APP_DIR=%~1"
if "%APP_DIR%"=="" set "APP_DIR=%cd%"
set "APP_DIR=!APP_DIR:"=!"
if "!APP_DIR:~-1!"=="\" set "APP_DIR=!APP_DIR:~0,-1!"

if not exist "!APP_DIR!\main.py" (
  echo [ERROR] "!APP_DIR!\main.py" not found.
  echo         Run from DataOpsStudio root or pass it as argument.
  pause
  exit /b 1
)

set "ENV_FILE=!APP_DIR!\.env"
if not exist "!ENV_FILE!" (
  echo .env not found - already in dev mode. Nothing to do.
  pause
  exit /b 0
)

REM Timestamp suffix so multiple disables don't overwrite each other
for /f "tokens=2 delims==" %%I in ('wmic os get LocalDateTime /value 2^>nul ^| find "="') do set "DT=%%I"
if defined DT (
  set "TS=!DT:~0,8!-!DT:~8,6!"
) else (
  set "TS=%date:~0,4%%date:~5,2%%date:~8,2%-%time:~0,2%%time:~3,2%%time:~6,2%"
  set "TS=!TS: =0!"
)

set "DISABLED_FILE=!APP_DIR!\.env.disabled.!TS!"

echo.
echo ============================================
echo  Disable PROD mode
echo  Dir    : !APP_DIR!
echo  From   : !ENV_FILE!
echo  To     : !DISABLED_FILE!
echo ============================================
echo.

move "!ENV_FILE!" "!DISABLED_FILE!" >nul
if errorlevel 1 (
  echo [ERROR] Failed to rename .env (file in use? close any editor first).
  pause
  exit /b 1
)

echo .env moved to .env.disabled.!TS!
echo.
echo  Next:
echo    1. Restart the app (close start.bat window, double-click start.bat)
echo    2. Now running in DEV mode (Guard dry-run, default dev JWT key)
echo    3. All sessions signed with the PROD secret are now invalid
echo       (users must re-login).
echo.
echo  To re-enable PROD: rename .env.disabled.!TS! back to .env
echo  Or run enable-prod.bat to generate a brand new secret.
echo ============================================
pause
endlocal
