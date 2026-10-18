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
