@echo off
rem Athena uninstaller - Windows (.bat) - the ROOT copy.
rem Removes Athena FULLY: the .athena home (code + venv + data), the
rem `athena` command, and the scheduled task (the Windows service).
rem
rem Usage: uninstall.bat [keep-data]
rem   keep-data    keep %USERPROFILE%\.athena (code/data) but remove the
rem                command + scheduled task
rem
rem Safety: this deletes %USERPROFILE%\.athena — your profiles, sessions,
rem memories, provider keys (.secret), everything.
setlocal

set "ATHENA_SYSTEM=%~dp0"
for %%I in ("%ATHENA_SYSTEM%..") do set "ATHENA_ROOT=%%~fI"
set "KEEP_DATA=false"
if /i "%~1"=="keep-data" set "KEEP_DATA=true"
set "TASK=AthenaAgent"

echo.
echo  ==============================================
echo   Athena Agent Uninstaller — Windows
echo  ==============================================
echo.
echo  This will remove Athena from:
echo    system:  %ATHENA_SYSTEM%
if "%KEEP_DATA%"=="false" echo    data:    %ATHENA_ROOT%  ^(profiles, sessions, memories, keys^)
echo    command: %USERPROFILE%\.local\bin\athena.bat
echo    service: scheduled task %TASK%
echo.

rem ---- 1. Remove the scheduled task ----
schtasks /query /tn "%TASK%" >nul 2>&1
if not errorlevel 1 (
    echo [uninstall] removing scheduled task %TASK%...
    schtasks /delete /f /tn "%TASK%" >nul 2>&1
    taskkill /f /im athena.exe >nul 2>&1
    echo [uninstall] task removed
)

rem ---- 2. Remove the command ----
del /q "%USERPROFILE%\.local\bin\athena.bat" >nul 2>&1
echo [uninstall] command removed: %USERPROFILE%\.local\bin\athena.bat

rem ---- 3. Remove the data home (unless keep-data) ----
if /i not "%KEEP_DATA%"=="true" (
    if exist "%ATHENA_ROOT%" (
        echo [uninstall] removing %ATHENA_ROOT% ...
        rmdir /s /q "%ATHENA_ROOT%"
        echo [uninstall] data removed
    )
) else (
    echo [uninstall] keeping %ATHENA_ROOT% ^(keep-data^)
)

echo.
echo  Athena has been uninstalled.
echo  ^(The release zip / extracted folder you ran this from is left untouched.^)
endlocal
