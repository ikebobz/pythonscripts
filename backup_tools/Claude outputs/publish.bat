@echo off
setlocal EnableExtensions
REM ==================================================================
REM  Release team: put new module JARs, core JAR, query.yml and/or SQL
REM  scripts in .\jars, edit manifest.json, then run this file.
REM  sha256, size, sequence and published_at are filled in automatically.
REM ==================================================================
cd /d "%~dp0"
if not exist release-private.pem (
    echo release-private.pem not found. First time only, run:
    echo   java -jar module-updater.jar keygen release-private.pem release-public.pem
    echo and ship release-public.pem with the facility installer.
    pause
    exit /b 1
)
if not exist jars mkdir jars

echo [1/2] Signing manifest.json ...
java -jar module-updater.jar sign release-private.pem manifest.json jars
if errorlevel 1 (
    echo [ERROR] Signing failed - see the message above. Nothing was published.
    pause
    exit /b 1
)

echo [2/2] Building USB bundle in usb-bundle\module-updates ...
if exist usb-bundle\module-updates rmdir /s /q usb-bundle\module-updates
mkdir usb-bundle\module-updates
if errorlevel 1 (
    echo [ERROR] Could not create usb-bundle\module-updates - is a file in it open?
    pause
    exit /b 1
)
copy /Y manifest.json usb-bundle\module-updates\ >nul
copy /Y manifest.json.sig usb-bundle\module-updates\ >nul
REM Copy every release file in jars\ (JARs, query.yml, .sql) - skipping the placeholder .keep
set COUNT=0
for %%F in (jars\*) do (
    if /i not "%%~nxF"==".keep" (
        copy /Y "%%F" usb-bundle\module-updates\ >nul
        set /a COUNT+=1
    )
)
echo       manifest + signature + %COUNT% release file(s) copied

echo.
echo Upload manifest.json, manifest.json.sig and the files in jars\ to the GitHub release.
echo For offline sites, copy usb-bundle\module-updates to the root of a USB stick.
pause
