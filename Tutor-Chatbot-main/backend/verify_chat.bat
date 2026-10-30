@echo off
REM verify_chat.bat - run the end-to-end check for #2 sources and #3 classrooms.
REM Usage: keep the server running (start_server.bat), then double-click this file.
setlocal
set "HERE=%~dp0"
set "PY=%HERE%venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] venv not found at %PY%
  echo         Run setup_backend.bat first.
  pause
  exit /b 1
)
"%PY%" "%HERE%verify_chat.py"
echo.
echo (Done. If you see CHAT ERROR or connection refused, make sure start_server.bat is running.)
pause
