[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskNames = @(
    "QuuDet-API",
    "QuuDet-LocalGPUAgent",
    "QuuDet-CeleryWorker",
    "QuuDet-CeleryBeat"
)
foreach ($TaskName in $TaskNames) {
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $Task) {
        continue
    }
    if ($PSCmdlet.ShouldProcess($TaskName, "Stop scheduled task")) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    if ($Remove -and $PSCmdlet.ShouldProcess($TaskName, "Remove scheduled task")) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
}
