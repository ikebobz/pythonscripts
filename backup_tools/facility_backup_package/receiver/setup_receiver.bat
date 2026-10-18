@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo === Backup receiver setup ===

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo [ERROR] Python 3 is not installed. Install it from https://www.python.org/downloads/
    echo         ^(tick "Add python.exe to PATH"^) and run setup_receiver.bat again.
    pause
    exit /b 1
)

REM ---- Shared token (created once, kept in receiver_config.bat) ------
set "TOKEN="
for /f "usebackq delims=" %%T in (`%PY% "%~dp0receive_server.py" --config "%~dp0receiver_config.bat" --init-token`) do set "TOKEN=%%T"
if not defined TOKEN (
    echo [ERROR] Could not create the token.
    pause
    exit /b 1
)
call "%~dp0receiver_config.bat"

REM ---- Firewall: allow the port from Tailscale addresses only (needs admin)
net session >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Not running as administrator, so the firewall rule was not added.
    echo           Right-click setup_receiver.bat ^> "Run as administrator" to add it.
    goto :task
)
netsh advfirewall firewall delete rule name="Facility Backup Receiver" >nul 2>&1
netsh advfirewall firewall add rule name="Facility Backup Receiver" dir=in action=allow protocol=TCP localport=%RECEIVER_PORT% remoteip=100.64.0.0/10
if errorlevel 1 echo [WARNING] Adding the firewall rule failed.

:task
set "PYW="
for /f "usebackq delims=" %%W in (`%PY% -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"`) do set "PYW=%%W"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_task.ps1" -TaskName "Facility Backup Receiver" -PythonW "%PYW%" -Script "%~dp0receive_server.py"

echo.
echo ================================================================
echo  Receiver is listening on port %RECEIVER_PORT%. Files go to:
echo    %DEST_DIR%
echo.
echo  In each facility's backup_config.bat set:
echo    set SHIP_URL=http://^<this PC's Tailscale IP or name^>:%RECEIVER_PORT%
echo    set SHIP_TOKEN=%TOKEN%
echo.
echo  This PC's Tailscale IP:
tailscale ip -4 2>nul || echo    (run "tailscale ip -4" or check the Tailscale app)
echo ================================================================
pause
