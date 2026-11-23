@echo off
REM athena.bat — start Athena on Windows: use the venv beside this file and hand off to the entry point.
setlocal
set "HERE=%~dp0"
set "VENV=%HERE%..\.venv"

if not exist "%VENV%\Scripts\python.exe" (
  echo missing venv at %VENV% — create it with: python -m venv "%VENV%" ^&^& "%VENV%\Scripts\pip" install -r "%HERE%requirements.txt"
  exit /b 1
)

set "PYTHONPATH=%HERE%;%PYTHONPATH%"
"%VENV%\Scripts\python.exe" -m cli %*
endlocal
