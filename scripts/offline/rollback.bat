@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM DataOps Studio - Rollback to backup
REM
REM Usage:
REM   rollback.bat                            (auto-pick latest backup_*)
REM   rollback.bat D:\DataOpsStudio\backup_20260527-141500
REM
REM Restores app / static / main.py / requirements.txt / docker-compose.yml /
REM BUILD_INFO.json from a backup_<TS> directory. User data (config / results /
REM logs / data / python / .venv) is untouched throughout.
REM ============================================================

set "BACKUP_DIR=%~1"

REM If no arg, find the newest backup_* in current dir
if "%BACKUP_DIR%"=="" (
  for /f "delims=" %%D in ('dir /B /AD /OD "backup_*" 2^>nul') do set "BACKUP_DIR=%cd%\%%D"
  if "!BACKUP_DIR!"=="" (
    echo [ERROR] No backup_* dir found in current directory.
    echo         Run from your DataOpsStudio root, or pass path:
    echo            rollback.bat D:\DataOpsStudio\backup_20260527-141500
    pause
    exit /b 1
  )
)

set "BACKUP_DIR=!BACKUP_DIR:"=!"
if "!BACKUP_DIR:~-1!"=="\" set "BACKUP_DIR=!BACKUP_DIR:~0,-1!"

if not exist "!BACKUP_DIR!\BACKUP_INFO.txt" (
  echo [ERROR] "!BACKUP_DIR!" is not a valid DataOps backup
  echo         (BACKUP_INFO.txt missing^).
  pause
  exit /b 1
)

REM Read target dir from BACKUP_INFO.txt
for /f "tokens=1,2 delims==" %%A in (!BACKUP_DIR!\BACKUP_INFO.txt) do (
  if "%%A"=="TARGET_DIR" set "TARGET_DIR=%%B"
)
if "!TARGET_DIR!"=="" (
  echo [ERROR] TARGET_DIR missing from BACKUP_INFO.txt.
  pause
  exit /b 1
)
set "TARGET_DIR=!TARGET_DIR:"=!"
if "!TARGET_DIR:~-1!"=="\" set "TARGET_DIR=!TARGET_DIR:~0,-1!"

echo.
echo ============================================
echo  Rollback
echo  Backup : !BACKUP_DIR!
echo  Target : !TARGET_DIR!
echo ============================================
echo.
echo IMPORTANT: stop the app first.
echo.
echo Press Ctrl+C to abort, any key to continue...
pause >nul

echo.
echo [1/2] Remove current code...
if exist "!TARGET_DIR!\app"        rd /S /Q "!TARGET_DIR!\app"
if exist "!TARGET_DIR!\static\spa" rd /S /Q "!TARGET_DIR!\static\spa"
echo   OK.

echo.
echo [2/2] Restore from backup...
if exist "!BACKUP_DIR!\app"                robocopy "!BACKUP_DIR!\app"    "!TARGET_DIR!\app"    /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
if exist "!BACKUP_DIR!\static"             robocopy "!BACKUP_DIR!\static" "!TARGET_DIR!\static" /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
if exist "!BACKUP_DIR!\main.py"            copy /Y "!BACKUP_DIR!\main.py"            "!TARGET_DIR!\"            >nul
if exist "!BACKUP_DIR!\requirements.txt"   copy /Y "!BACKUP_DIR!\requirements.txt"   "!TARGET_DIR!\"            >nul
if exist "!BACKUP_DIR!\docker-compose.yml" copy /Y "!BACKUP_DIR!\docker-compose.yml" "!TARGET_DIR!\"            >nul
if exist "!BACKUP_DIR!\BUILD_INFO.json"    copy /Y "!BACKUP_DIR!\BUILD_INFO.json"    "!TARGET_DIR!\"            >nul
echo   OK.

echo.
echo ============================================
echo  Rollback complete.
echo  Next: run start.bat in target dir.
echo ============================================
pause
endlocal
