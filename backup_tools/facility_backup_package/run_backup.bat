@echo off
setlocal EnableExtensions
cd /d "%~dp0"

call "%~dp0backup_config.bat"

REM ---- Locate Python ------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo [ERROR] Python 3 was not found. Run setup.bat first.
    exit /b 1
)

REM ---- Password ------------------------------------------------------
if "%DB_PASSWORD%"=="" (
    for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "$p = Read-Host 'Database password for %DB_USER%' -AsSecureString; [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($p))"`) do set "DB_PASSWORD=%%P"
)
if "%DB_PASSWORD%"=="" (
    echo [ERROR] No database password supplied.
    exit /b 1
)
REM Passed via environment, not the command line, so it is not visible in process lists
set "PGPASSWORD=%DB_PASSWORD%"

REM ---- Logging ------------------------------------------------------
if not exist "%~dp0logs" mkdir "%~dp0logs"
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%T"
set "LOG=%~dp0logs\backup_%STAMP%.log"

echo Starting incremental backup of %DB_NAME% on %DB_HOST%:%DB_PORT% ...
echo Output folder: %BACKUP_DIR%
echo Log file:      %LOG%

%PY% "%~dp0backup_script.py" ^
    --csv "%~dp0data_tables.csv" ^
    --output "%BACKUP_DIR%" ^
    --last-run "%INITIAL_LAST_RUN%" ^
    --host "%DB_HOST%" ^
    --port %DB_PORT% ^
    --dbname "%DB_NAME%" ^
    --user "%DB_USER%" > "%LOG%" 2>&1

set "RC=%ERRORLEVEL%"
set "PGPASSWORD="
if not "%RC%"=="0" (
    echo [FAILED] Backup failed with code %RC%. See %LOG%
    type "%LOG%"
    exit /b %RC%
)
echo [OK] Backup completed. Archives are in %BACKUP_DIR%
exit /b 0
