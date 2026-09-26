@echo off
REM 一键本地启动（Windows）
cd /d %~dp0

echo [1/2] Starting API on :8000 ...
start "nl-screener-api" cmd /k "cd /d %~dp0apps\api && set PYTHONPATH=. && set USE_MOCK_DATA=true && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

timeout /t 2 /nobreak >nul

echo [2/2] Starting Web on :5173 ...
start "nl-screener-web" cmd /k "cd /d %~dp0apps\web && npm run dev -- --host 127.0.0.1 --port 5173"

echo.
echo Open http://127.0.0.1:5173
echo API  http://127.0.0.1:8000/docs
