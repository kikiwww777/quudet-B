@echo off
chcp 65001 >nul
cd /d "%~dp0"
set ROOT=%CD%

echo ========================================
echo  QuuDet YOLO Lab - Quick Start
echo ========================================

:: 1. Start database
echo [1/4] Starting PostgreSQL + Redis...
docker compose up db redis -d
if %errorlevel% neq 0 (
    echo [ERROR] Docker failed. Make sure Docker Desktop is running.
    pause
    exit /b 1
)
echo   OK

:: 2. Database migration
echo [2/4] Running database migration...
cd /d "%ROOT%\quudet-yolo-lab-backend"
call .venv\Scripts\python.exe -m alembic upgrade head
if %errorlevel% neq 0 (
    echo [ERROR] Migration failed.
    pause
    exit /b 1
)
echo   OK

:: 3. Start API
echo [3/4] Starting API (port 8000)...
start "QuuDet-API" /MIN cmd /c "cd /d %ROOT%\quudet-yolo-lab-backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
timeout /t 3 /nobreak >nul
echo   OK

:: 4. Start frontend
echo [4/4] Starting frontend (port 8080)...
start "QuuDet-Frontend" /MIN cmd /c "cd /d %ROOT%\quudet-yolo-lab && ..\quudet-yolo-lab-backend\.venv\Scripts\python.exe -m http.server 8080 --bind 0.0.0.0"
echo   OK

timeout /t 2 /nobreak >nul

echo ========================================
echo  All services started!
echo  API:    http://localhost:8000/docs
echo  Web UI: http://localhost:8080
echo.
echo  For Celery Worker (new terminal):
echo  cd %ROOT%\quudet-yolo-lab-backend
echo  .venv\Scripts\python.exe -m celery -A app.celery_app worker -l info --pool=solo
echo ========================================
pause
