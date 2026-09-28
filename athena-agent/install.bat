@echo off
rem ===========================================================================
rem Athena Agent Installer — Windows (.bat)
rem ===========================================================================
rem THE 08-16 DUMB-INSTALL RULE: Athena ALWAYS installs into %USERPROFILE%\.athena
rem no matter where this script is run from (Downloads, Desktop, home).
rem The destination is fixed; the source is wherever the script + code are.
rem
rem Usage (PowerShell):
rem   powershell -Command "Invoke-WebRequest -Uri <url>/install.bat -OutFile install.bat; .\install.bat"
rem
rem Or from the release zip: run install.bat from the extracted folder.
rem ===========================================================================
setlocal enabledelayedexpansion

rem Configuration
set "ATHENA_ROOT=%ATHENA_ROOT%"
if "%ATHENA_ROOT%"=="" set "ATHENA_ROOT=%USERPROFILE%\.athena"
rem THE DUMB-INSTALL RULE: the destination is ALWAYS ~/.athena.
set "INSTALL_DIR=%ATHENA_ROOT%\athena-system"
set "BRANCH=main"

rem The source: where the code currently is (the folder this script sits in).
set "SRC_DIR=%~dp0"
set "CODE_SRC="
if exist "%SRC_DIR%athena.py" if exist "%SRC_DIR%requirements.txt" (
    set "CODE_SRC=%SRC_DIR%"
)

echo.
echo  ==============================================
echo   Athena Agent Installer — Windows
echo  ==============================================
echo.

rem ---- 1. Python present? ----
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found on PATH. Install Python 3.10+ and re-run.
    exit /b 1
)
for /f "delims=" %%v in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set "PYVER=%%v"
echo [OK] python !PYVER! found

rem ---- 2. git present? ----
git --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] git not found on PATH. Install git and re-run.
    exit /b 1
)

rem ---- 3. Get the code into the .athena home ----
if exist "%INSTALL_DIR%\athena.py" if exist "%INSTALL_DIR%\requirements.txt" (
    echo [..] Athena already installed at %INSTALL_DIR% - keeping the code
    goto :venv
)
if defined CODE_SRC (
    rem The zip-extract case: copy the code into the canonical .athena home.
    echo [..] Copying the Athena code from %SRC_DIR% to %INSTALL_DIR%...
    if not exist "%ATHENA_ROOT%" mkdir "%ATHENA_ROOT%"
    if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
    xcopy /e /i /y /q "%SRC_DIR%*" "%INSTALL_DIR%\" >nul
    echo [OK] Code copied to %INSTALL_DIR%
    goto :venv
)
if exist "%INSTALL_DIR%" (
    echo [ERROR] Directory exists but is not a git repo: %INSTALL_DIR%
    echo         Remove it or set ATHENA_ROOT to a fresh location.
    exit /b 1
)
echo [..] Cloning Athena (branch: %BRANCH%)...
mkdir "%ATHENA_ROOT%" 2>nul
git clone --depth 1 --branch %BRANCH% https://github.com/FenrirLupus/Athena-Agent.git "%INSTALL_DIR%"
if errorlevel 1 (
    echo [ERROR] Failed to clone repository
    exit /b 1
)
echo [OK] Cloned via HTTPS

:venv
rem ---- 4. Virtual environment (idempotent: keep a working venv) ----
echo [..] Checking virtual environment...
if exist "%ATHENA_ROOT%\.venv\Scripts\python.exe" (
    echo [OK] Virtual environment already present - kept
    goto :deps
)
echo [..] Creating virtual environment...
python -m venv "%ATHENA_ROOT%\.venv"
if errorlevel 1 (
    echo [ERROR] Failed to create the virtual environment
    exit /b 1
)

:deps
rem ---- 5. Dependencies ----
echo [..] Installing dependencies...
"%ATHENA_ROOT%\.venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel >nul
"%ATHENA_ROOT%\.venv\Scripts\python.exe" -m pip install -r "%INSTALL_DIR%\requirements.txt"
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies
    exit /b 1
)
echo [OK] Dependencies installed

rem ---- 6. The athena command ----
echo [..] Linking the 'athena' command...
set "BIN_DIR=%ATHENA_BIN%"
if "%BIN_DIR%"=="" set "BIN_DIR=%USERPROFILE%\.local\bin"
if not exist "%BIN_DIR%" mkdir "%BIN_DIR%"
rem THE 08-16 ROOT-LAUNCHER LAYOUT (the CEO's spec): the launcher lives
rem at athena-system/launcher.bat (NOT launchers/windows-launcher.bat) —
rem all 4 install/launcher files are in the root.
copy /y "%INSTALL_DIR%\launcher.bat" "%BIN_DIR%\athena.bat" >nul
echo [OK] athena command linked: %BIN_DIR%\athena.bat

rem ---- 7. Seed the runtime dirs ----
echo [..] Seeding the runtime dirs...
mkdir "%ATHENA_ROOT%\profiles" 2>nul
mkdir "%ATHENA_ROOT%\workflows" 2>nul
mkdir "%ATHENA_ROOT%\skills" 2>nul
mkdir "%ATHENA_ROOT%\tools" 2>nul
mkdir "%ATHENA_ROOT%\plugins" 2>nul
echo [OK] Runtime dirs ready at %ATHENA_ROOT%

echo.
echo  ==============================================
echo   Athena Agent installed!
echo    system:  %INSTALL_DIR%
echo    data:    %ATHENA_ROOT%
echo    command: athena
echo  ==============================================
echo.
echo  Athena is SET UP in your home folder: %ATHENA_ROOT%
echo  The release ZIP and this extracted folder are now just
echo  PORTABLE COPIES - they are NOT used by Athena. You may
echo  safely delete them (the zip + this duplicate folder).
echo.
echo  Next: run 'athena setup' to configure providers, then 'athena web'
echo  to start the GUI server.
echo.
endlocal
