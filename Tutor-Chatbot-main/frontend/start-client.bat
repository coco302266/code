@echo off
rem ===================================================================
rem  AI Classroom - one-click desktop client launcher
rem
rem  Double-click this file to open the client.
rem  It will start the frontend dev server (if not running) and then
rem  open the desktop window. Close the window to stop everything.
rem
rem  Keep this file ASCII-only: cmd.exe reads .bat in the system
rem  codepage (GBK on CN Windows), so non-ASCII text here would be
rem  garbled in the console.
rem ===================================================================

setlocal
cd /d "%~dp0"

rem Some Electron hosts export this and it forces electron.exe to run
rem as plain Node, which crashes on startup. Clear it.
set ELECTRON_RUN_AS_NODE=

echo.
echo   ============================================
echo    AI Classroom  -  desktop client
echo   ============================================
echo.
echo   Opening the client window...
echo   First launch may take ~20s while the frontend compiles.
echo.
echo   To close everything: just close the window.
echo.

call npm run client

echo.
echo   Client closed.
pause
