@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM Import existing DB drivers (dmPython / oracledb / ibm_db)
REM from your system Python into the portable Python.
REM
REM Usage:
REM   import-db-drivers.bat <SYSTEM_PY_SITE_PACKAGES> <PORTABLE_DIR>
REM
REM Example:
REM   import-db-drivers.bat ^
REM     C:\Users\wangds\AppData\Local\Programs\Python\Python312\Lib\site-packages ^
REM     D:\DataOpsStudio-portable-20260526
REM
REM How to find your system site-packages path:
REM   python -c "import site; print(site.getsitepackages())"
REM ============================================================

set "SYS_SP=%~1"
set "PORTABLE_DIR=%~2"

if "%SYS_SP%"=="" (
  echo.
  set /p "SYS_SP=Path to your system Python site-packages: "
)
if "%PORTABLE_DIR%"=="" (
  echo.
  set /p "PORTABLE_DIR=Path to portable package dir [contains python\]: "
)

set "SYS_SP=!SYS_SP:"=!"
set "PORTABLE_DIR=!PORTABLE_DIR:"=!"
if "!SYS_SP:~-1!"=="\" set "SYS_SP=!SYS_SP:~0,-1!"
if "!PORTABLE_DIR:~-1!"=="\" set "PORTABLE_DIR=!PORTABLE_DIR:~0,-1!"

if not exist "!SYS_SP!" (
  echo [ERROR] Source not found: "!SYS_SP!"
  pause
  exit /b 1
)
if not exist "!PORTABLE_DIR!\python\python.exe" (
  echo [ERROR] Target not a portable package: "!PORTABLE_DIR!"
  echo         Expected "!PORTABLE_DIR!\python\python.exe" to exist.
  pause
  exit /b 1
)

set "DST=!PORTABLE_DIR!\python\Lib\site-packages"

echo.
echo === Importing DB drivers ===
echo From : !SYS_SP!
echo To   : !DST!
echo.

REM ---- dmPython ----
echo [1/3] dmPython ...
set "FOUND_DM=0"
if exist "!SYS_SP!\dmPython" (
  xcopy /S /Y /I /Q "!SYS_SP!\dmPython"             "!DST!\dmPython"             >nul
  set "FOUND_DM=1"
)
for %%F in ("!SYS_SP!\dmPython*.pyd") do (
  copy /Y "%%F" "!DST!\" >nul
  set "FOUND_DM=1"
)
for /d %%D in ("!SYS_SP!\dmPython-*.dist-info") do (
  xcopy /S /Y /I /Q "%%D"                            "!DST!\%%~nxD"               >nul
)
REM Some dmPython packages also ship DLLs as top-level files (libdmdpi.dll etc.)
for %%F in ("!SYS_SP!\..\..\DLLs\dmdpi*.dll" "!SYS_SP!\..\dmdpi*.dll") do (
  if exist "%%F" copy /Y "%%F" "!DST!\" >nul
)
if "!FOUND_DM!"=="1" (echo       OK) else (echo       SKIP ^(not found^))

REM ---- oracledb ----
echo [2/3] oracledb ...
set "FOUND_OR=0"
if exist "!SYS_SP!\oracledb" (
  xcopy /S /Y /I /Q "!SYS_SP!\oracledb"              "!DST!\oracledb"             >nul
  set "FOUND_OR=1"
)
for /d %%D in ("!SYS_SP!\oracledb-*.dist-info") do (
  xcopy /S /Y /I /Q "%%D"                            "!DST!\%%~nxD"               >nul
)
if "!FOUND_OR!"=="1" (echo       OK) else (echo       SKIP ^(not found^))

REM ---- ibm_db (and ibm_db_dbi / ibm_db_sa) ----
echo [3/3] ibm_db family ...
set "FOUND_IBM=0"
for %%F in ("!SYS_SP!\ibm_db*.pyd") do (
  copy /Y "%%F" "!DST!\" >nul
  set "FOUND_IBM=1"
)
if exist "!SYS_SP!\ibm_db_dbi.py" (
  copy /Y "!SYS_SP!\ibm_db_dbi.py" "!DST!\" >nul
  set "FOUND_IBM=1"
)
if exist "!SYS_SP!\ibm_db_dbi" (
  xcopy /S /Y /I /Q "!SYS_SP!\ibm_db_dbi"            "!DST!\ibm_db_dbi"           >nul
  set "FOUND_IBM=1"
)
if exist "!SYS_SP!\ibm_db_sa" (
  xcopy /S /Y /I /Q "!SYS_SP!\ibm_db_sa"             "!DST!\ibm_db_sa"            >nul
)
for /d %%D in ("!SYS_SP!\ibm_db-*.dist-info") do (
  xcopy /S /Y /I /Q "%%D"                            "!DST!\%%~nxD"               >nul
)
REM ibm_db ships clidriver DLLs that may live in package dir
if exist "!SYS_SP!\clidriver" (
  xcopy /S /Y /I /Q "!SYS_SP!\clidriver"             "!DST!\clidriver"            >nul
)
if "!FOUND_IBM!"=="1" (echo       OK) else (echo       SKIP ^(not found^))

echo.
echo ============================================
echo  Verify (run portable python):
echo.
"!PORTABLE_DIR!\python\python.exe" -c "import importlib; [print('  '+m+': '+('OK' if importlib.util.find_spec(m) else 'MISSING')) for m in ['dmPython','oracledb','ibm_db','ibm_db_dbi','pymysql']]"
echo.
echo Next: run start.bat in "!PORTABLE_DIR!"
echo ============================================
pause
endlocal
