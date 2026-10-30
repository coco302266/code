@echo off
set BASE=D:\STUDY\AI-Classroom\Tutor-Chatbot-main\backend
set PY=C:\Users\24264\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe
cd /d "%BASE%"

echo ============================================
echo  AI-Classroom backend: setup and self-test
echo ============================================

echo [1/5] Prepare venv ...
if exist "venv\Scripts\python.exe" (
  venv\Scripts\python.exe --version >nul 2>&1
  if errorlevel 1 (
    echo   old venv broken, rebuilding ...
    rmdir /s /q venv
    "%PY%" -m venv venv
  ) else (
    echo   venv OK, skip rebuild
  )
) else (
  echo   creating venv ...
  "%PY%" -m venv venv
)

echo [2/5] Install dependencies via China mirror (needs internet) ...
call venv\Scripts\activate.bat
echo   trying Tsinghua mirror ...
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn --retries 5 --timeout 60
if errorlevel 1 (
  echo   Tsinghua failed, trying Aliyun mirror ...
  pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com --retries 5 --timeout 60
)
if errorlevel 1 (
  echo.
  echo  [ERROR] pip install failed on all mirrors.
  echo  Your pc cannot reach PyPI or China mirrors. Possible causes:
  echo   1) No internet at all - open a browser to confirm.
  echo   2) Company proxy required - ask IT for proxy address, then set before running:
  echo      set HTTPS_PROXY=http://proxy_host:port
  echo      set HTTP_PROXY=http://proxy_host:port
  echo  (This internet is on YOUR pc, not the chat tool's network.)
  pause
  exit /b 1
)

echo [3/5] Check .env ...
if not exist ".env" (
  copy env.example .env >nul
  echo   created .env from env.example
)
echo  Please open backend\.env with Notepad and fill LLM_API_KEY.
echo  (run_tests / smoke_rag do NOT need the key)

echo [4/5] run_tests.py ...
python run_tests.py

echo [5/5] smoke_rag.py ...
python smoke_rag.py

echo.
echo  ============ DONE ============
echo  If both tests above are green, the code is OK.
echo  To test real chat: fill LLM_API_KEY in .env, then double-click start_server.bat,
echo  open http://127.0.0.1:8000/health in browser.
echo.
pause
