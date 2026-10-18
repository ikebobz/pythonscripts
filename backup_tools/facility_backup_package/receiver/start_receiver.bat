@echo off
setlocal EnableExtensions
cd /d "%~dp0"
REM Runs the receiver in this window (normally it runs hidden as a background
REM task created by setup_receiver.bat).
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo [ERROR] Python 3 was not found.
    exit /b 1
)
echo Log file: %~dp0logs\receiver.log     (Ctrl+C to stop)
%PY% "%~dp0receive_server.py" --config "%~dp0receiver_config.bat" %*
