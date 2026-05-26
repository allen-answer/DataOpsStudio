@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul

REM ============================================================
REM DataOps Studio 全量代码升级(保留 config / results / logs / data)
REM
REM 行为:
REM   - 备份现场代码到 backup_before_full_upgrade_<TS>\
REM   - rd /S /Q 旧 app\ static\spa\ templates\ 防文件残留
REM   - robocopy 覆盖代码 + 拷根 .py / .bat / BUILD_INFO.json
REM   - **不动**:config/<runtime>.json / metadata_cache/ / sql_templates.json
REM             results/ / logs/ / data/ / .venv/
REM ============================================================

set "PATCH_DIR=%~dp0"
if "%~1"=="" (
  set "TARGET_DIR=%CD%"
  if not exist "!TARGET_DIR!\main.py" (
    for %%I in ("%PATCH_DIR%..") do set "PARENT_DIR=%%~fI"
    if exist "!PARENT_DIR!\main.py" set "TARGET_DIR=!PARENT_DIR!"
  )
) else (
  set "TARGET_DIR=%~1"
)

if not exist "!TARGET_DIR!\main.py" (
  echo [ERROR] Target directory doesn't look like DataOps Studio root:
  echo         "!TARGET_DIR!"
  echo Usage: upgrade.bat D:\DataOpsStudio
  pause
  exit /b 1
)

REM ---- 注意:如果 DataOps Studio 正在运行,请先关闭 start.bat 窗口
REM      否则下面 rd /S /Q 删 app\ 会因文件被 Python 占用失败
for /f "delims=" %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TS=%%I"
if "%TS%"=="" set "TS=session"
set "BACKUP_DIR=!TARGET_DIR!\backup_before_full_upgrade_%TS%"

echo === DataOps Studio Full Code Upgrade ===
echo Patch : "%PATCH_DIR%"
echo Target: "!TARGET_DIR!"
echo Backup: "!BACKUP_DIR!"
echo.

mkdir "!BACKUP_DIR!" >nul 2>nul

if exist "!TARGET_DIR!\app" robocopy "!TARGET_DIR!\app" "!BACKUP_DIR!\app" /E >nul
if exist "!TARGET_DIR!\static" robocopy "!TARGET_DIR!\static" "!BACKUP_DIR!\static" /E >nul
if exist "!TARGET_DIR!\templates" robocopy "!TARGET_DIR!\templates" "!BACKUP_DIR!\templates" /E >nul
if exist "!TARGET_DIR!\init_db" robocopy "!TARGET_DIR!\init_db" "!BACKUP_DIR!\init_db" /E >nul
if exist "!TARGET_DIR!\main.py" copy /Y "!TARGET_DIR!\main.py" "!BACKUP_DIR!\main.py" >nul
if exist "!TARGET_DIR!\requirements.txt" copy /Y "!TARGET_DIR!\requirements.txt" "!BACKUP_DIR!\requirements.txt" >nul
if exist "!TARGET_DIR!\start.bat" copy /Y "!TARGET_DIR!\start.bat" "!BACKUP_DIR!\start.bat" >nul
if exist "!TARGET_DIR!\install.bat" copy /Y "!TARGET_DIR!\install.bat" "!BACKUP_DIR!\install.bat" >nul
if exist "!TARGET_DIR!\BUILD_INFO.json" copy /Y "!TARGET_DIR!\BUILD_INFO.json" "!BACKUP_DIR!\BUILD_INFO.json" >nul

echo [1/3] Clear old code dirs (防已删除文件残留)...
if exist "!TARGET_DIR!\app" rd /S /Q "!TARGET_DIR!\app"
if exist "!TARGET_DIR!\static\spa" rd /S /Q "!TARGET_DIR!\static\spa"
if exist "!TARGET_DIR!\templates" rd /S /Q "!TARGET_DIR!\templates"

echo [2/3] Copy new code (app / static / templates / init_db)...
if exist "%PATCH_DIR%app" robocopy "%PATCH_DIR%app" "!TARGET_DIR!\app" /E >nul
if exist "%PATCH_DIR%static" robocopy "%PATCH_DIR%static" "!TARGET_DIR!\static" /E >nul
if exist "%PATCH_DIR%templates" robocopy "%PATCH_DIR%templates" "!TARGET_DIR!\templates" /E >nul
if exist "%PATCH_DIR%init_db" robocopy "%PATCH_DIR%init_db" "!TARGET_DIR!\init_db" /E >nul

echo [3/3] Copy root files (main.py / requirements.txt / *.bat / BUILD_INFO.json)...
if exist "%PATCH_DIR%main.py" copy /Y "%PATCH_DIR%main.py" "!TARGET_DIR!\main.py" >nul
if exist "%PATCH_DIR%requirements.txt" copy /Y "%PATCH_DIR%requirements.txt" "!TARGET_DIR!\requirements.txt" >nul
if exist "%PATCH_DIR%README_OFFLINE.md" copy /Y "%PATCH_DIR%README_OFFLINE.md" "!TARGET_DIR!\README_OFFLINE.md" >nul
if exist "%PATCH_DIR%start.bat" copy /Y "%PATCH_DIR%start.bat" "!TARGET_DIR!\start.bat" >nul
if exist "%PATCH_DIR%install.bat" copy /Y "%PATCH_DIR%install.bat" "!TARGET_DIR!\install.bat" >nul
if exist "%PATCH_DIR%BUILD_INFO.json" copy /Y "%PATCH_DIR%BUILD_INFO.json" "!TARGET_DIR!\BUILD_INFO.json" >nul

REM example config 只在用户 config 缺对应文件时填(不覆盖现有)
if not exist "!TARGET_DIR!\config" mkdir "!TARGET_DIR!\config"
if exist "%PATCH_DIR%config" (
  for %%f in ("%PATCH_DIR%config\*.example.json") do (
    if not exist "!TARGET_DIR!\config\%%~nxf" copy /Y "%%f" "!TARGET_DIR!\config\%%~nxf" >nul
  )
)

echo.
echo ============================================
echo Upgrade complete.
echo Backup: !BACKUP_DIR!
echo.
echo Preserved (not touched):
echo   config\datasources.json / tasks.json / users.json / projects.json
echo   config\jobs.json / workflows.json / sql_workbench.json
echo   config\metadata_cache\ / sql_templates.json
echo   results\ / logs\ / data\ / .venv\
echo.
echo Next: 双击 start.bat,浏览器 Ctrl+F5 强刷
echo ============================================
pause
endlocal
