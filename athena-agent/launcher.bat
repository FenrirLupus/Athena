@echo off
rem Athena launcher - Windows (.bat) - the ROOT copy (the CEO's layout:
rem all 4 install/launcher files live in athena-system/, not launchers/).
rem Thin wrapper: all logic lives in athena.py (SAME directory).
rem THE 08-16 SELF-HEALING FIX: when the venv is missing (a wipe, a fresh
rem extract), the launcher REBUILDS it from requirements.txt instead of
rem silently falling back to the system python (which lacks fastapi).
rem The venv is Athena's OWN environment — never a shared runtime.
setlocal
set "ATHENA_SYSTEM=%~dp0"
for %%I in ("%ATHENA_SYSTEM%..") do set "ATHENA_ROOT=%%~fI"

set "VENV_DIR=%ATHENA_ROOT%\.venv"
set "ATHENA_PY=%VENV_DIR%\Scripts\python.exe"

rem ---- Self-healing: rebuild the venv if missing ----
if not exist "%ATHENA_PY%" (
    echo [athena] .venv missing - rebuilding from requirements.txt...
    where python >nul 2>&1
    if errorlevel 1 (
        echo [athena] ERROR: python not found on PATH - need python3 to build the venv.
        exit /b 1
    )
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [athena] ERROR: failed to create the venv.
        exit /b 1
    )
    "%ATHENA_PY%" -m pip install --upgrade pip setuptools wheel >nul 2>&1
    "%ATHENA_PY%" -m pip install -r "%ATHENA_SYSTEM%\requirements.txt"
    if errorlevel 1 (
        echo [athena] ERROR: dependency install failed.
        exit /b 1
    )
    echo [athena] .venv rebuilt + dependencies installed
)

"%ATHENA_PY%" "%ATHENA_SYSTEM%\athena.py" %*
exit /b %ERRORLEVEL%
