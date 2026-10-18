@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0backup_config.bat"

echo === Facility backup setup ===

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo [ERROR] Python 3 is not installed. Install it from https://www.python.org/downloads/
    echo         ^(tick "Add python.exe to PATH"^) and run setup.bat again.
    pause
    exit /b 1
)

echo Installing Python dependencies...
%PY% -m pip install --upgrade -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [ERROR] Dependency install failed. Check the internet connection.
    pause
    exit /b 1
)

echo.
set "SCHED="
set /p SCHED=Create a daily scheduled backup at %SCHEDULE_TIME%? (Y/N):
if /i not "%SCHED%"=="Y" goto :ship
if "%DB_PASSWORD%"=="" echo [WARNING] DB_PASSWORD is blank in backup_config.bat - scheduled runs will fail until it is set.
schtasks /Create /F /SC DAILY /ST %SCHEDULE_TIME% /TN "Facility Incremental Backup" /TR "\"%~dp0run_backup.bat\""

:ship
echo.
set "SHIP="
set /p SHIP=Send new backups automatically to the central computer over Tailscale? (Y/N):
if /i not "%SHIP%"=="Y" goto :done
if "%SHIP_URL%"=="" goto :shipconfig
if "%SHIP_TOKEN%"=="" goto :shipconfig

set "PYW="
for /f "usebackq delims=" %%W in (`%PY% -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"`) do set "PYW=%%W"
if not defined PYW (
    echo [ERROR] Could not locate pythonw.exe.
    goto :done
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_task.ps1" -TaskName "Facility Backup Shipper" -PythonW "%PYW%" -Script "%~dp0ship_backups.py"
echo Shipper log: %~dp0logs\ship.log
goto :done

:shipconfig
echo [WARNING] SHIP_URL and SHIP_TOKEN must be filled in backup_config.bat first.
echo           Then run setup.bat again and answer Y to this question.

:done
echo.
echo Setup complete. Run run_backup.bat to take a backup now.
pause
