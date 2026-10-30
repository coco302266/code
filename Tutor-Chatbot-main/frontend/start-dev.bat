@echo off
rem ===================================================================
rem  AI Classroom - one-click dev launcher (browser mode)
rem
rem  Double-click this file to start everything:
rem    [1] mock backend   ->  http://127.0.0.1:8000
rem    [2] frontend dev   ->  http://localhost:3000
rem  then it opens http://localhost:3000/ in your default browser.
rem
rem  Two console windows stay open - close both to stop the servers.
rem
rem  Keep this file ASCII-only: cmd.exe reads .bat in the system
rem  codepage (GBK on CN Windows), so non-ASCII text here would be
rem  garbled in the console.
rem ===================================================================

setlocal
cd /d "%~dp0"

rem Some Electron hosts export this and it forces node/electron to run
rem in the wrong mode. Clear it before spawning anything.
set ELECTRON_RUN_AS_NODE=

echo.
echo   ============================================
echo    AI Classroom  -  dev launcher
echo   ============================================
echo.
echo   [1/2] mock backend  -^>  http://127.0.0.1:8000
echo   [2/2] frontend dev  -^>  http://localhost:3000
echo.
echo   Two windows will stay open. Close both to stop.
echo.

start "mock-backend" cmd /k "node mock-backend\server.js"
timeout /t 2 /nobreak >nul
start "frontend-dev" cmd /k "npm run dev"
timeout /t 6 /nobreak >nul

start "" http://localhost:3000/

echo   Browser opened. If the page is blank, wait a few seconds
echo   and refresh, or open this URL by hand:
echo.
echo       http://localhost:3000/
echo.
echo   Tip: routes need a trailing slash, e.g. /teacher/
echo.
pause
