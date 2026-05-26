@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM DataOps Studio - Code Upgrade
REM
REM Usage:
REM   upgrade.bat <TARGET_DIR>
REM
REM Example:
REM   upgrade.bat D:\DataOpsStudio
REM
REM No auto-detection. You MUST pass the target install path,
REM or enter it when prompted. The script never guesses.
REM
REM This script:
REM   - Backs up app / static / templates / main.py to backup_<TS>\
REM   - Replaces them with new code from this package
REM   - Preserves config\, results\, logs\, data\, .venv\
REM ============================================================

set "PATCH_DIR=%~dp0"
set "TARGET_DIR=%~1"

if "%TARGET_DIR%"=="" (
  echo.
  set /p "TARGET_DIR=Enter DataOps Studio install path [e.g. D:\DataOpsStudio]: "
)

if "!TARGET_DIR!"=="" (
  echo [ERROR] No target path given.
  pause
  exit /b 1
)

REM Strip surrounding quotes
set "TARGET_DIR=!TARGET_DIR:"=!"

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

REM Timestamp (locale-agnostic via WMIC if available, else simple)
for /f "tokens=2 delims==" %%I in ('wmic os get LocalDateTime /value 2^>nul ^| find "="') do set "DT=%%I"
if defined DT (
  set "TS=!DT:~0,8!-!DT:~8,6!"
) else (
  set "TS=%date:~0,4%%date:~5,2%%date:~8,2%-%time:~0,2%%time:~3,2%%time:~6,2%"
  set "TS=!TS: =0!"
)
set "BACKUP_DIR=!TARGET_DIR!\backup_!TS!"

echo.
echo === DataOps Studio Code Upgrade ===
echo Patch  : %PATCH_DIR%
echo Target : !TARGET_DIR!
echo Backup : !BACKUP_DIR!
echo.
echo Press Ctrl+C to abort, any key to continue...
pause >nul

mkdir "!BACKUP_DIR!" >nul 2>nul

echo.
echo [1/4] Backup existing code...
if exist "!TARGET_DIR!\app"              robocopy "!TARGET_DIR!\app"              "!BACKUP_DIR!\app"              /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
if exist "!TARGET_DIR!\static"           robocopy "!TARGET_DIR!\static"           "!BACKUP_DIR!\static"           /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
if exist "!TARGET_DIR!\templates"        robocopy "!TARGET_DIR!\templates"        "!BACKUP_DIR!\templates"        /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
if exist "!TARGET_DIR!\init_db"          robocopy "!TARGET_DIR!\init_db"          "!BACKUP_DIR!\init_db"          /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
if exist "!TARGET_DIR!\main.py"          copy /Y "!TARGET_DIR!\main.py"          "!BACKUP_DIR!\main.py"          >nul
if exist "!TARGET_DIR!\requirements.txt" copy /Y "!TARGET_DIR!\requirements.txt" "!BACKUP_DIR!\requirements.txt" >nul
if exist "!TARGET_DIR!\start.bat"        copy /Y "!TARGET_DIR!\start.bat"        "!BACKUP_DIR!\start.bat"        >nul
if exist "!TARGET_DIR!\install.bat"      copy /Y "!TARGET_DIR!\install.bat"      "!BACKUP_DIR!\install.bat"      >nul
if exist "!TARGET_DIR!\BUILD_INFO.json"  copy /Y "!TARGET_DIR!\BUILD_INFO.json"  "!BACKUP_DIR!\BUILD_INFO.json"  >nul

echo [2/4] Remove old code dirs...
if exist "!TARGET_DIR!\app"        rd /S /Q "!TARGET_DIR!\app"
if exist "!TARGET_DIR!\static\spa" rd /S /Q "!TARGET_DIR!\static\spa"
if exist "!TARGET_DIR!\templates"  rd /S /Q "!TARGET_DIR!\templates"

echo [3/4] Copy new code...
if exist "%PATCH_DIR%app"       robocopy "%PATCH_DIR%app"       "!TARGET_DIR!\app"       /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
if exist "%PATCH_DIR%static"    robocopy "%PATCH_DIR%static"    "!TARGET_DIR!\static"    /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
if exist "%PATCH_DIR%templates" robocopy "%PATCH_DIR%templates" "!TARGET_DIR!\templates" /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
if exist "%PATCH_DIR%init_db"   robocopy "%PATCH_DIR%init_db"   "!TARGET_DIR!\init_db"   /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul

echo [4/4] Copy root files...
if exist "%PATCH_DIR%main.py"           copy /Y "%PATCH_DIR%main.py"           "!TARGET_DIR!\main.py"           >nul
if exist "%PATCH_DIR%requirements.txt"  copy /Y "%PATCH_DIR%requirements.txt"  "!TARGET_DIR!\requirements.txt"  >nul
if exist "%PATCH_DIR%README_OFFLINE.md" copy /Y "%PATCH_DIR%README_OFFLINE.md" "!TARGET_DIR!\README_OFFLINE.md" >nul
if exist "%PATCH_DIR%start.bat"         copy /Y "%PATCH_DIR%start.bat"         "!TARGET_DIR!\start.bat"         >nul
if exist "%PATCH_DIR%install.bat"       copy /Y "%PATCH_DIR%install.bat"       "!TARGET_DIR!\install.bat"       >nul
if exist "%PATCH_DIR%BUILD_INFO.json"   copy /Y "%PATCH_DIR%BUILD_INFO.json"   "!TARGET_DIR!\BUILD_INFO.json"   >nul

if not exist "!TARGET_DIR!\config" mkdir "!TARGET_DIR!\config"
if exist "%PATCH_DIR%config" (
  for %%f in ("%PATCH_DIR%config\*.example.json") do (
    if not exist "!TARGET_DIR!\config\%%~nxf" copy /Y "%%f" "!TARGET_DIR!\config\%%~nxf" >nul
  )
)

echo.
echo ============================================
echo  Upgrade complete.
echo  Backup : !BACKUP_DIR!
echo  Next   : run start.bat in target dir
echo ============================================
pause
endlocal
