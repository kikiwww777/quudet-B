# QuuDet Windows Persistent Agent

## Problem

Do not launch `celery`, `app.agent.runner`, `execute_job`, `run_in_background`, or `nohup` from an AI chat terminal for a long training run. The terminal host can close its child-process tree when its session expires. A GPU job then loses its executor and later becomes stale during reconciliation.

Training, validation, and detection jobs use the unified node path:

```text
API -> PENDING_ASSIGN -> local node agent -> yolo subprocess
```

Celery is only used for maintenance work such as reconciliation. It is not the GPU training executor in this path.

## Persistent Local Node

On Windows, register the API and GPU agent as current-user Scheduled Tasks:

```powershell
cd C:\path\to\quudet
.\scripts\windows\install-local-services.ps1 -Start
```

After updating the Windows service scripts, use `-RestartRunning` once so already-running task wrappers reload the new files. This stops any active local job, so do not use it while training is in progress:

```powershell
.\scripts\windows\install-local-services.ps1 -RestartRunning -Start
```

This creates:

| Task | Responsibility |
|---|---|
| `QuuDet-API` | Starts only after database and Redis are reachable, then runs the local FastAPI process. |
| `QuuDet-LocalGPUAgent` | Starts only after the configured master API reports `/readyz`; it claims node-scheduled GPU jobs and keeps their heartbeats alive. |
| `QuuDet-CeleryWorker` | Starts after the local API readiness gate and runs `celery -A app.celery_app worker -l info --pool=solo`. |
| `QuuDet-CeleryBeat` | Starts after the local API readiness gate and runs the reconciliation scheduler. |

All tasks start at user logon and run in the Task Scheduler host, not under the terminal that registered them. They are configured to continue on battery power and ignore duplicate starts. The startup sequence is `PostgreSQL + Redis -> API /readyz -> agent, worker, beat`. If the dependencies do not become ready within one minute, the relevant task exits once with a clear log message rather than retrying forever. Unexpected process exits are retried with bounded backoff (five attempts); inspect the component's stderr log after that limit.

Before using the local stack, start Docker Desktop and the dependencies:

```powershell
docker compose up db redis -d
.\scripts\windows\install-local-services.ps1 -Start
```

If Docker Desktop is stopped, `DATABASE_URL` points to an unavailable PostgreSQL server, or Redis is unavailable, do not keep the scheduled tasks running. Start the dependencies first, then manually start the failed task from Task Scheduler or rerun the command above.

The default node is `gpu-node-01`. Set a unique ID for another machine:

```powershell
.\scripts\windows\install-local-services.ps1 -Start `
  -NodeId "laptop-3050" `
  -NodeName "RTX 3050 laptop" `
  -MasterApiBase "http://MASTER_HOST:8000"
```

If the master has node authentication enabled, also pass `-NodeToken`.

## Operations

```powershell
# Inspect task state
Get-ScheduledTask -TaskName "QuuDet-*" | Select-Object TaskName, State

# Tail service logs
Get-Content .\quudet-yolo-lab-backend\service-logs\agent.log -Wait
Get-Content .\quudet-yolo-lab-backend\service-logs\api.log -Wait
Get-Content .\quudet-yolo-lab-backend\service-logs\agent.stderr.log -Wait

# Verify the API readiness contract
Invoke-RestMethod http://127.0.0.1:8000/readyz

# Stop both services without removing their logon registration
.\scripts\windows\stop-local-services.ps1

# Stop and delete both tasks
.\scripts\windows\stop-local-services.ps1 -Remove
```

`start-all.bat` now registers and starts these tasks automatically. Do not use `start /B` or chat-terminal background commands to create another local GPU agent.

## Recovery Contract

1. A terminal or AI session closing must not terminate either scheduled task.
2. If an agent process crashes, its scheduled-task wrapper restarts it and it re-registers its node.
3. If the machine reboots, the tasks restart at user logon.
4. If the entire machine loses power during a run, the existing reconciliation timeout marks the interrupted job failed. A new retry must be submitted; a killed YOLO subprocess cannot safely resume from an arbitrary point.
