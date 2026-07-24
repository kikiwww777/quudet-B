@echo off
cd /d "%~dp0"

echo ========================================
echo  QuuDet YOLO Lab — 一键启动
echo ========================================
echo.

:: ── 1. 启动数据库 ──────────────────────────
echo [1/4] Starting PostgreSQL + Redis...
docker compose up db redis -d
if %errorlevel% neq 0 (
    echo [!] Docker 启动失败，请检查 Docker Desktop 是否运行中
    pause
    exit /b 1
)
echo   OK
echo.

:: ── 2. 启动 API ────────────────────────────
echo [2/4] Starting persistent API + GPU agent supervisors...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\install-local-services.ps1" -RestartRunning -Start
if %errorlevel% neq 0 (
    echo [!] QuuDet service registration failed.
    pause
    exit /b 1
)
timeout /t 4 /nobreak >nul
echo   OK
echo.

:: ── 3. 启动前端 ────────────────────────────
echo [3/4] Starting frontend (port 8080)...
start "QuuDet-Web" /B "quudet-yolo-lab-backend\.venv\Scripts\python.exe" -m http.server 8080 --bind 0.0.0.0 --directory "quudet-yolo-lab"
echo   OK
echo.

:: ── 4. 启动本地 GPU 节点 ───────────────────
echo [4/4] Checking local GPU agent scheduled task...
schtasks /Query /TN "QuuDet-LocalGPUAgent" >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Local GPU agent task was not registered.
    pause
    exit /b 1
)
echo   OK
echo.

echo ========================================
echo  All services started!
echo  API:    http://localhost:8000
echo  Web:    http://localhost:8080
echo  Worker: GPU node (gpu-node-01, Windows Task Scheduler)
echo ========================================
echo.
echo  API and GPU agent now survive terminal/session closure.
echo  Stop them with scripts\windows\stop-local-services.ps1.
pause
