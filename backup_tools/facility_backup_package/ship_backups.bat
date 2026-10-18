@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0backup_config.bat"

REM Runs the backup shipper in this window (normally it runs hidden as a
REM scheduled task created by setup.bat). Add --once to send what is waiting
REM and exit, e.g.:  ship_backups.bat --once

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo [ERROR] Python 3 was not found. Run setup.bat first.
    exit /b 1
)
if "%SHIP_URL%"=="" goto :noconfig
if "%SHIP_TOKEN%"=="" goto :noconfig

echo Sending new backups from %BACKUP_DIR% to %SHIP_URL%
echo Log file: %~dp0logs\ship.log     (Ctrl+C to stop)
%PY% "%~dp0ship_backups.py" --config "%~dp0backup_config.bat" %*
exit /b %ERRORLEVEL%

:noconfig
echo [ERROR] Set SHIP_URL and SHIP_TOKEN in backup_config.bat first.
exit /b 1
