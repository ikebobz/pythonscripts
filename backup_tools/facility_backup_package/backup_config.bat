@echo off
REM ==================================================================
REM  Facility backup settings - edit these values for each facility
REM ==================================================================

REM PostgreSQL connection
set DB_HOST=localhost
set DB_PORT=5432
set DB_NAME=lgh
set DB_USER=postgres

REM Password: leave blank to be prompted at run time.
REM For unattended (scheduled) runs it must be filled in here.
set DB_PASSWORD=lamis

REM Where backup zips and manifest.json are written
set BACKUP_DIR=%~dp0backups

REM Starting point for the FIRST run only (no manifest.json yet).
REM 1970-01-01 means the first run captures all existing records.
REM After that, manifest.json tracks the last run automatically.
set INITIAL_LAST_RUN=2026-10-01T00:00:00+00:00

REM Time of day for the scheduled daily backup (24h HH:MM)
set SCHEDULE_TIME=11:00

REM ------------------------------------------------------------------
REM  Sending backups to the central computer over Tailscale
REM ------------------------------------------------------------------
REM Receiver address: http://<Tailscale IP or machine name>:8765
REM e.g. http://100.101.102.103:8765   Leave blank to turn shipping off.
set SHIP_URL=

REM Shared secret: copy RECEIVER_TOKEN from the receiver's receiver_config.bat
set SHIP_TOKEN=

REM Name of this facility's folder on the receiver (letters, digits, - _ .)
set FACILITY_ID=%COMPUTERNAME%
