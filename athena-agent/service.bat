@echo off
rem Athena service control - Windows (.bat) - the ROOT copy.
rem Installs + starts Athena as a scheduled task at logon (auto-start on
rem boot + crash-restart). Windows only (Linux uses service.sh).
rem Usage: service.bat [install|start|stop|status|uninstall]
setlocal
set "ATHENA_SYSTEM=%~dp0"
for %%I in ("%ATHENA_SYSTEM%..") do set "ATHENA_ROOT=%%~fI"
set "TASK=AthenaAgent"

set "CMD=%~1"
if "%CMD%"=="" set "CMD=install"

if /i "%CMD%"=="install" (
    echo [service] installing task %TASK% - starts at logon...
    schtasks /create /f /tn "%TASK%" /tr "\"%ATHENA_SYSTEM%launcher.bat\" web" /sc onlogon
    if errorlevel 1 (
        echo [service] failed to register the task - run as Administrator?
        exit /b 1
    )
    echo [service] starting...
    schtasks /run /tn "%TASK%"
    echo [service] installed - check the GUI at http://127.0.0.1:51420
    exit /b 0
)

if /i "%CMD%"=="start" (
    schtasks /run /tn "%TASK%"
    echo [service] started
    exit /b 0
)

if /i "%CMD%"=="stop" (
    taskkill /f /im athena.exe >nul 2>&1
    echo [service] stopped
    exit /b 0
)

if /i "%CMD%"=="status" (
    schtasks /query /tn "%TASK%" 2>nul
    if errorlevel 1 (
        echo [service] NOT installed
    ) else (
        echo [service] task registered - check the GUI at http://127.0.0.1:51420
    )
    exit /b 0
)

if /i "%CMD%"=="uninstall" (
    schtasks /delete /f /tn "%TASK%"
    echo [service] uninstalled
    exit /b 0
)

echo usage: service.bat [install^|start^|stop^|status^|uninstall]
exit /b 1
