@echo off
set BASE=D:\STUDY\AI-Classroom\Tutor-Chatbot-main\backend
cd /d "%BASE%"
call venv\Scripts\activate.bat
REM Clear any proxy so the DeepSeek call does not route through a dead proxy (502).
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=
set all_proxy=
echo Starting backend (Ctrl+C to stop) ...
echo After start, open http://127.0.0.1:8000/health in browser
python -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
