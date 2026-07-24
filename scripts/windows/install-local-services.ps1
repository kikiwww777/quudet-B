[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$NodeId = "gpu-node-01",
    [string]$NodeName = "",
    [string]$NodeToken = "",
    [string]$MasterApiBase = "http://127.0.0.1:8000",
    [ValidateRange(1, 8)]
    [int]$MaxConcurrency = 1,
    [switch]$SkipApi,
    [switch]$SkipAgent,
    [switch]$SkipCeleryWorker,
    [switch]$SkipCeleryBeat,
    [ValidateRange(10, 600)]
    [int]$ApiReadyTimeoutSeconds = 90,
    [switch]$RestartRunning,
    [switch]$Start
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "Windows Task Scheduler is required."
}

$Runner = Join-Path $PSScriptRoot "run-windows-service.ps1"
$PrerequisiteLibrary = Join-Path $PSScriptRoot "service-prerequisites.ps1"
if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Supervisor runner not found: $Runner"
}
if (-not (Test-Path -LiteralPath $PrerequisiteLibrary)) {
    throw "Service prerequisite library not found: $PrerequisiteLibrary"
}
. $PrerequisiteLibrary

$PowerShellExe = Join-Path $PSHOME "powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShellExe)) {
    $PowerShellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
}
$Identity = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { "$env:COMPUTERNAME\$env:USERNAME" }
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650)

function Quote-TaskArgument([string]$Value) {
    '"' + $Value.Replace('"', '\"') + '"'
}

function Register-QuuDetTask([string]$TaskName, [string[]]$Arguments) {
    $Action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument ($Arguments -join " ")
    if ($PSCmdlet.ShouldProcess($TaskName, "Register Windows scheduled task")) {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $Action `
            -Trigger $Trigger `
            -Settings $Settings `
            -User $Identity `
            -RunLevel Limited `
            -Force | Out-Null
    }
}

function Start-QuuDetTask([string]$TaskName) {
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ($Task.State -ne "Running" -and $PSCmdlet.ShouldProcess($TaskName, "Start Windows scheduled task")) {
        Start-ScheduledTask -TaskName $TaskName
    }
}

function Stop-QuuDetTaskIfRunning([string]$TaskName) {
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -ne $Task -and $Task.State -eq "Running" -and $PSCmdlet.ShouldProcess($TaskName, "Stop running Windows scheduled task before update")) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    }
}

if ($RestartRunning) {
    foreach ($taskName in @("QuuDet-API", "QuuDet-LocalGPUAgent", "QuuDet-CeleryWorker", "QuuDet-CeleryBeat")) {
        Stop-QuuDetTaskIfRunning $taskName
    }
}

$Common = @(
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy", "Bypass",
    "-File", (Quote-TaskArgument $Runner)
)

if (-not $SkipApi) {
    Register-QuuDetTask "QuuDet-API" ($Common + @("-Component", "api"))
}

if (-not $SkipAgent) {
    $AgentArguments = $Common + @(
        "-Component", "agent",
        "-NodeId", (Quote-TaskArgument $NodeId),
        "-MasterApiBase", (Quote-TaskArgument $MasterApiBase),
        "-MaxConcurrency", "$MaxConcurrency"
    )
    if ($NodeName) {
        $AgentArguments += @("-NodeName", (Quote-TaskArgument $NodeName))
    }
    if ($NodeToken) {
        $AgentArguments += @("-NodeToken", (Quote-TaskArgument $NodeToken))
    }
    Register-QuuDetTask "QuuDet-LocalGPUAgent" $AgentArguments
}

if (-not $SkipCeleryWorker) {
    Register-QuuDetTask "QuuDet-CeleryWorker" ($Common + @("-Component", "celery-worker"))
}

if (-not $SkipCeleryBeat) {
    Register-QuuDetTask "QuuDet-CeleryBeat" ($Common + @("-Component", "celery-beat"))
}

if ($Start -and -not $WhatIfPreference) {
    if (-not $SkipApi) {
        Start-QuuDetTask "QuuDet-API"
        $apiReady = Wait-QuuDetApiReady `
            -ApiBase "http://127.0.0.1:8000" `
            -TimeoutSeconds $ApiReadyTimeoutSeconds `
            -OnStatus { param($Message) Write-Host "[QuuDet] $Message" }
        if (-not $apiReady) {
            throw "QuuDet API did not become ready. Check quudet-yolo-lab-backend\\service-logs\\api.log and api.stderr.log; dependencies must be ready before starting the remaining tasks."
        }
    }
    elseif (-not $SkipAgent) {
        $masterReady = Wait-QuuDetApiReady `
            -ApiBase $MasterApiBase `
            -TimeoutSeconds $ApiReadyTimeoutSeconds `
            -OnStatus { param($Message) Write-Host "[QuuDet] $Message" }
        if (-not $masterReady) {
            throw "Master API did not become ready: $MasterApiBase. The agent task was not started."
        }
    }

    foreach ($taskName in @(
        if (-not $SkipAgent) { "QuuDet-LocalGPUAgent" }
        if (-not $SkipCeleryWorker) { "QuuDet-CeleryWorker" }
        if (-not $SkipCeleryBeat) { "QuuDet-CeleryBeat" }
    )) {
        Start-QuuDetTask $taskName
    }
}

if (-not $WhatIfPreference) {
    Get-ScheduledTask -TaskName "QuuDet-*" -ErrorAction SilentlyContinue |
        Select-Object TaskName, State |
        Format-Table -AutoSize
    Write-Host "Service logs: $(Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'quudet-yolo-lab-backend\service-logs')"
}
