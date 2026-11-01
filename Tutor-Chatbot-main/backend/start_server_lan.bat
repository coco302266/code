@echo off
rem 双机联调用：监听 0.0.0.0，让同一个局域网里的另一台电脑能连上。
rem 本机自测仍走 http://127.0.0.1:8000；对方用 http://<本机局域网IP>:8000。
rem 注意：绑 0.0.0.0 之后，同一网段的人都能访问 —— 联调结束请换回 start_server.bat。
set "BASE=%~dp0"
cd /d "%BASE%"
call venv\Scripts\activate.bat
REM 代理必须清掉，否则 /chat 会 502（见 AGENTS.md 第四节）
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=
set all_proxy=
echo Starting backend on 0.0.0.0:8000 (LAN mode, Ctrl+C to stop) ...
echo Local:  http://127.0.0.1:8000/health
echo LAN:    http://^<your-lan-ip^>:8000/health   (ipconfig 查 IPv4)
echo.
echo Remember: ALLOWED_ORIGINS in .env must include the other machine's origin,
echo   e.g. ALLOWED_ORIGINS=["http://localhost:3000","http://192.168.1.23:3000"]
echo otherwise the browser will block every request (CORS).
python -m uvicorn app:app --host 0.0.0.0 --port 8000
pause
