[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet("api", "agent", "celery-worker", "celery-beat")]
    [string]$Component,
    [string]$NodeId = "gpu-node-01",
    [string]$NodeName = "",
    [string]$NodeToken = "",
    [string]$MasterApiBase = "http://127.0.0.1:8000",
    [ValidateRange(1, 8)]
    [int]$MaxConcurrency = 1,
    [ValidateRange(2, 300)]
    [int]$RestartDelaySeconds = 10,
    [ValidateRange(5, 600)]
    [int]$DependencyTimeoutSeconds = 60,
    [ValidateRange(1, 30)]
    [int]$DependencyPollSeconds = 2,
    [ValidateRange(1, 20)]
    [int]$MaxConsecutiveRestarts = 5,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Backend = Join-Path $Root "quudet-yolo-lab-backend"
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
$LogDir = Join-Path $Backend "service-logs"
$PrerequisiteLibrary = Join-Path $PSScriptRoot "service-prerequisites.ps1"

if ($env:OS -ne "Windows_NT") {
    throw "This supervisor is only supported on Windows."
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python virtual environment not found: $Python"
}
if (-not (Test-Path -LiteralPath $PrerequisiteLibrary)) {
    throw "Service prerequisite library not found: $PrerequisiteLibrary"
}
. $PrerequisiteLibrary

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogPath = Join-Path $LogDir "$Component.log"
$StdoutLogPath = Join-Path $LogDir "$Component.stdout.log"
$StderrLogPath = Join-Path $LogDir "$Component.stderr.log"

function Write-ServiceLog([string]$Message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$stamp] $Message"
    Add-Content -LiteralPath $LogPath -Value $line
    Write-Host $line
}

function Invoke-QuuDetProcess {
    $previousErrorAction = $ErrorActionPreference
    $supportsNativeErrorPreference = Test-Path Variable:PSNativeCommandUseErrorActionPreference
    if ($supportsNativeErrorPreference) {
        $previousNativeErrorPreference = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }
    $ErrorActionPreference = "Continue"
    try {
        if ($Component -eq "api") {
            & $Python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 1>> $StdoutLogPath 2>> $StderrLogPath
        }
        elseif ($Component -eq "agent") {
            $env:NODE_KIND = "local"
            $env:NODE_ID = $NodeId
            $env:NODE_NAME = if ($NodeName) { $NodeName } else { $NodeId }
            $env:MASTER_API_BASE = $MasterApiBase
            $env:NODE_MAX_CONCURRENCY = "$MaxConcurrency"
            if ($NodeToken) {
                $env:NODE_TOKEN = $NodeToken
            }
            else {
                Remove-Item Env:NODE_TOKEN -ErrorAction SilentlyContinue
            }
            & $Python -m app.agent.runner 1>> $StdoutLogPath 2>> $StderrLogPath
        }
        elseif ($Component -eq "celery-worker") {
            & $Python -m celery -A app.celery_app worker -l info --pool=solo 1>> $StdoutLogPath 2>> $StderrLogPath
        }
        else {
            & $Python -m celery -A app.celery_app beat -l info 1>> $StdoutLogPath 2>> $StderrLogPath
        }
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
        if ($supportsNativeErrorPreference) {
            $PSNativeCommandUseErrorActionPreference = $previousNativeErrorPreference
        }
    }
}

function Test-ComponentPrerequisites {
    if ($Component -eq "api") {
        return Wait-QuuDetRuntimeDependencies `
            -Backend $Backend `
            -TimeoutSeconds $DependencyTimeoutSeconds `
            -PollSeconds $DependencyPollSeconds `
            -OnStatus { param($Message) Write-ServiceLog $Message }
    }

    $apiBase = if ($Component -eq "agent") { $MasterApiBase } else { "http://127.0.0.1:8000" }
    return Wait-QuuDetApiReady `
        -ApiBase $apiBase `
        -TimeoutSeconds $DependencyTimeoutSeconds `
        -PollSeconds $DependencyPollSeconds `
        -OnStatus { param($Message) Write-ServiceLog $Message }
}

Push-Location $Backend
try {
    if (-not (Test-ComponentPrerequisites)) {
        Write-ServiceLog "Prerequisites did not become ready within $DependencyTimeoutSeconds seconds. Supervisor will not restart automatically. Start Docker/PostgreSQL/Redis, then start the scheduled task again."
        exit 2
    }
    if ($PreflightOnly) {
        Write-ServiceLog "Preflight succeeded for $Component."
        exit 0
    }

    $consecutiveRestarts = 0
    while ($consecutiveRestarts -lt $MaxConsecutiveRestarts) {
        Write-ServiceLog "Starting $Component process."
        Invoke-QuuDetProcess
        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0) {
            Write-ServiceLog "$Component process exited cleanly; supervisor is stopping."
            exit 0
        }

        $consecutiveRestarts++
        if ($consecutiveRestarts -ge $MaxConsecutiveRestarts) {
            Write-ServiceLog "$Component process exited with code $exitCode $MaxConsecutiveRestarts times. Supervisor is stopping to prevent an infinite restart loop. Check $StderrLogPath."
            exit $exitCode
        }
        if (-not (Test-ComponentPrerequisites)) {
            Write-ServiceLog "Prerequisites became unavailable after $Component exited with code $exitCode. Supervisor is stopping instead of retrying."
            exit 2
        }

        $delay = [Math]::Min($RestartDelaySeconds * $consecutiveRestarts, 60)
        Write-ServiceLog "$Component process exited with code $exitCode; retry $consecutiveRestarts/$MaxConsecutiveRestarts in $delay seconds."
        Start-Sleep -Seconds $delay
    }
}
catch {
    Write-ServiceLog "Fatal supervisor error: $($_.Exception.Message)"
    exit 1
}
finally {
    Pop-Location
}
