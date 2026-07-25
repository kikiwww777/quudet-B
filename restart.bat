@echo off
cd /d "D:\Developer\quudet"

echo Starting database services (PostgreSQL + Redis)...
docker compose up db redis -d
timeout /t 5 /nobreak >nul

echo Killing existing processes on ports 8000 and 8080...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTEN"') do taskkill /f /pid %%a 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080" ^| findstr "LISTEN"') do taskkill /f /pid %%a 2>nul
timeout /t 2 /nobreak >nul

echo Running Alembic migrations...
"quudet-yolo-lab-backend\.venv\Scripts\python.exe" -m alembic -c quudet-yolo-lab-backend/alembic.ini upgrade head

echo Starting Celery worker...
start "QuuDet-Worker" /B "quudet-yolo-lab-backend\.venv\Scripts\python.exe" -m celery -A app.celery_app worker -l info --working-directory "quudet-yolo-lab-backend"

echo Starting Celery beat (periodic reconciliation)...
start "QuuDet-Beat" /B "quudet-yolo-lab-backend\.venv\Scripts\python.exe" -m celery -A app.celery_app beat -l info --working-directory "quudet-yolo-lab-backend"

echo Starting API backend...
start "QuuDet-API" /B "quudet-yolo-lab-backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --working-directory "quudet-yolo-lab-backend"

timeout /t 3 /nobreak >nul

echo Starting local training agent...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%CD%\quudet-yolo-lab-backend\scripts\start-local-agent.ps1"

echo Starting frontend...
start "QuuDet-Web" /B "quudet-yolo-lab-backend\.venv\Scripts\python.exe" -m http.server 8080 --bind 0.0.0.0 --directory "quudet-yolo-lab"

echo.
echo ========================================
echo  QuuDet YOLO Lab started!
echo  Beat:  Celery (periodic reconciliation)
echo  Worker: Celery (background)
echo  API:    http://127.0.0.1:8000
echo  Web:    http://127.0.0.1:8080
echo ========================================
pause
