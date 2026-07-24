$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Frontend = Join-Path $Root "quudet-yolo-lab"
$ServiceInstaller = Join-Path $Root "scripts\windows\install-local-services.ps1"
$ServiceStopper = Join-Path $Root "scripts\windows\stop-local-services.ps1"
$Python = Join-Path $Root "quudet-yolo-lab-backend\.venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}
if (-not (Test-Path $ServiceInstaller)) {
    throw "QuuDet service installer not found: $ServiceInstaller"
}
if (-not (Test-Path $ServiceStopper)) {
    throw "QuuDet service stopper not found: $ServiceStopper"
}

function Stop-PortProcess($Port) {
    Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { $_ -and $_ -ne 0 } |
        ForEach-Object {
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        }
}

& $ServiceStopper
Stop-PortProcess 8000
Stop-PortProcess 8080
Start-Sleep -Seconds 1

& $ServiceInstaller -SkipAgent -Start

Start-Process -FilePath $Python `
    -ArgumentList @("-m", "http.server", "8080", "--bind", "0.0.0.0") `
    -WorkingDirectory $Frontend `
    -RedirectStandardOutput (Join-Path $Frontend "web.stdout.log") `
    -RedirectStandardError (Join-Path $Frontend "web.stderr.log") `
    -WindowStyle Hidden

Write-Host "QuuDet Windows master started after API readiness passed."
Write-Host "API: http://127.0.0.1:8000"
Write-Host "Web: http://127.0.0.1:8080"
