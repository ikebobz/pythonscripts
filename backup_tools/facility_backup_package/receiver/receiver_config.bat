@echo off
REM ==================================================================
REM  Backup receiver settings (central computer)
REM ==================================================================

REM Where received backups are stored: <DEST_DIR>\<FACILITY_ID>\*.zip
set DEST_DIR=%~dp0received

REM Network address to listen on. 0.0.0.0 = all; setup_receiver.bat adds a
REM firewall rule that only lets Tailscale addresses (100.64.0.0/10) connect.
set RECEIVER_BIND=0.0.0.0
set RECEIVER_PORT=8765

REM Shared secret. setup_receiver.bat fills this in; copy the same value into
REM SHIP_TOKEN in every facility's backup_config.bat.
set RECEIVER_TOKEN=
