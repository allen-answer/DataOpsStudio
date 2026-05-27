@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM DataOps Studio - Incremental Upgrade (20260526 -> 20260527)
REM
REM Usage:
REM   update.bat                          (will prompt for path)
REM   update.bat D:\DataOpsStudio         (path as argument)
REM
REM What this does:
REM   1. Validate target dir contains main.py (sanity check)
REM   2. Backup target's app / static / main.py / etc to backup_<TS>\
REM   3. Replace code with new version from this package
REM   4. Copy new sample configs (.env.example / config.yml.example)
REM      WITHOUT overwriting user's existing .env / config.yml
REM   5. Preserve user data: config\ results\ logs\ data\ python\ .venv\
REM
REM If anything goes wrong run rollback.bat in the same target dir.
REM ============================================================

set "PATCH_DIR=%~dp0"
set "TARGET_DIR=%~1"

if "%TARGET_DIR%"=="" (
  echo.
  set /p "TARGET_DIR=Enter DataOps Studio install path (e.g. D:\DataOpsStudio): "
)
if "!TARGET_DIR!"=="" (
  echo [ERROR] No target path given.
  pause
  exit /b 1
)
set "TARGET_DIR=!TARGET_DIR:"=!"
if "!TARGET_DIR:~-1!"=="\" set "TARGET_DIR=!TARGET_DIR:~0,-1!"

if not exist "!TARGET_DIR!\main.py" (
  echo [ERROR] "!TARGET_DIR!\main.py" not found.
  echo         The path must be the DataOps Studio root directory.
  pause
  exit /b 1
)
if not exist "%PATCH_DIR%app" (
  echo [ERROR] "%PATCH_DIR%app" not found.
  echo         This script must run from inside the upgrade package.
  pause
  exit /b 1
)

REM ---- Timestamp ----
for /f "tokens=2 delims==" %%I in ('wmic os get LocalDateTime /value 2^>nul ^| find "="') do set "DT=%%I"
if defined DT (
  set "TS=!DT:~0,8!-!DT:~8,6!"
) else (
  set "TS=%date:~0,4%%date:~5,2%%date:~8,2%-%time:~0,2%%time:~3,2%%time:~6,2%"
  set "TS=!TS: =0!"
)
set "BACKUP_DIR=!TARGET_DIR!\backup_!TS!"

echo.
echo ============================================
echo  DataOps Studio Incremental Upgrade
echo  Patch  : %PATCH_DIR%
echo  Target : !TARGET_DIR!
echo  Backup : !BACKUP_DIR!
echo ============================================
echo.
echo IMPORTANT: stop the app first (close start.bat window or
echo            'docker compose down' if you use docker).
echo.
echo Press Ctrl+C to abort, any key to continue...
pause >nul

mkdir "!BACKUP_DIR!" >nul 2>nul
if not exist "!BACKUP_DIR!" (
  echo [ERROR] Cannot create backup dir "!BACKUP_DIR!".
  pause
  exit /b 1
)

echo.
echo [1/5] Backup existing code to !BACKUP_DIR! ...
if exist "!TARGET_DIR!\app"                robocopy "!TARGET_DIR!\app"                "!BACKUP_DIR!\app"                /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
if exist "!TARGET_DIR!\static"             robocopy "!TARGET_DIR!\static"             "!BACKUP_DIR!\static"             /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
if exist "!TARGET_DIR!\main.py"            copy /Y "!TARGET_DIR!\main.py"            "!BACKUP_DIR!\"            >nul
if exist "!TARGET_DIR!\requirements.txt"   copy /Y "!TARGET_DIR!\requirements.txt"   "!BACKUP_DIR!\"            >nul
if exist "!TARGET_DIR!\docker-compose.yml" copy /Y "!TARGET_DIR!\docker-compose.yml" "!BACKUP_DIR!\"            >nul
if exist "!TARGET_DIR!\BUILD_INFO.json"    copy /Y "!TARGET_DIR!\BUILD_INFO.json"    "!BACKUP_DIR!\"            >nul
if exist "!TARGET_DIR!\start.bat"          copy /Y "!TARGET_DIR!\start.bat"          "!BACKUP_DIR!\"            >nul

REM Record backup metadata for rollback.bat
> "!BACKUP_DIR!\BACKUP_INFO.txt" (
  echo BACKUP_TIME=!TS!
  echo TARGET_DIR=!TARGET_DIR!
  echo PATCH_VERSION=20260527-incremental
)
echo   OK.

echo.
echo [2/5] Remove old code dirs (app, static\spa)...
if exist "!TARGET_DIR!\app"        rd /S /Q "!TARGET_DIR!\app"
if exist "!TARGET_DIR!\static\spa" rd /S /Q "!TARGET_DIR!\static\spa"
echo   OK.

echo.
echo [3/5] Copy new code...
robocopy "%PATCH_DIR%app"    "!TARGET_DIR!\app"    /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
robocopy "%PATCH_DIR%static" "!TARGET_DIR!\static" /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
copy /Y "%PATCH_DIR%main.py"            "!TARGET_DIR!\"            >nul
copy /Y "%PATCH_DIR%requirements.txt"   "!TARGET_DIR!\"            >nul
copy /Y "%PATCH_DIR%docker-compose.yml" "!TARGET_DIR!\"            >nul
copy /Y "%PATCH_DIR%BUILD_INFO.json"    "!TARGET_DIR!\"            >nul
REM start.bat upgraded to v2 (loads .env for prod mode). Original is backed up.
if exist "%PATCH_DIR%start.bat" copy /Y "%PATCH_DIR%start.bat" "!TARGET_DIR!\start.bat" >nul
REM Drop enable-prod.bat / disable-prod.bat into target for future use.
if exist "%PATCH_DIR%enable-prod.bat"  copy /Y "%PATCH_DIR%enable-prod.bat"  "!TARGET_DIR!\enable-prod.bat"  >nul
if exist "%PATCH_DIR%disable-prod.bat" copy /Y "%PATCH_DIR%disable-prod.bat" "!TARGET_DIR!\disable-prod.bat" >nul
echo   OK.

echo.
echo [4/5] Copy sample configs (without overwriting user files)...
REM .env.example is a TEMPLATE - never overwrite user .env
if exist "%PATCH_DIR%.env.example" copy /Y "%PATCH_DIR%.env.example" "!TARGET_DIR!\.env.example" >nul
REM config.yml.example -> config\ as sample
if not exist "!TARGET_DIR!\config" mkdir "!TARGET_DIR!\config" >nul
if exist "%PATCH_DIR%config.yml.example" copy /Y "%PATCH_DIR%config.yml.example" "!TARGET_DIR!\config\config.yml.example" >nul
REM CONFIG_REFERENCE.md as a doc
if exist "%PATCH_DIR%CONFIG_REFERENCE.md" copy /Y "%PATCH_DIR%CONFIG_REFERENCE.md" "!TARGET_DIR!\CONFIG_REFERENCE.md" >nul
echo   OK.

echo.
echo [5/5] Ensure data\ exists (SQLite folder)...
if not exist "!TARGET_DIR!\data" mkdir "!TARGET_DIR!\data" >nul
echo   OK.

REM Also drop rollback.bat into target dir so user can rollback later
if exist "%PATCH_DIR%rollback.bat" copy /Y "%PATCH_DIR%rollback.bat" "!TARGET_DIR!\rollback.bat" >nul

echo.
echo ============================================
echo  Upgrade complete.
echo.
echo  Backup        : !BACKUP_DIR!
echo  To rollback   : run rollback.bat in target dir
echo                  or: rollback.bat "!BACKUP_DIR!"
echo  Next          : run start.bat in target dir
echo.
echo  NOTE: start.bat has been upgraded to v2 (loads .env at startup).
echo        Your original start.bat is in the backup folder.
echo.
echo  Optional: switch to PROD mode in one click:
echo    1. Run: enable-prod.bat   (auto-generates JWT secret + writes .env)
echo    2. Restart: start.bat
echo  To go back to dev mode:
echo    Run: disable-prod.bat     (renames .env to .env.disabled)
echo ============================================
pause
endlocal
