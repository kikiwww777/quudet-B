$ErrorActionPreference = 'Stop'
$backendRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $backendRoot '.venv\Scripts\python.exe'
$logDirectory = Join-Path $backendRoot 'service-logs'
if (!(Test-Path $python)) { throw "Local agent Python executable not found: $python" }
$running = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'app\.agent\.runner' -and $_.CommandLine -match 'control-gpu-01' }
if ($running) { exit 0 }
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$env:MASTER_API_BASE = 'http://127.0.0.1:8000'
$env:NODE_ID = 'control-gpu-01'
$env:NODE_NAME = 'Control GPU 01'
$env:NODE_KIND = 'local'
$env:NODE_MAX_CONCURRENCY = '1'
Start-Process -FilePath $python -ArgumentList '-m', 'app.agent.runner' -WorkingDirectory $backendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'control-agent.stdout.log') -RedirectStandardError (Join-Path $logDirectory 'control-agent.stderr.log')
