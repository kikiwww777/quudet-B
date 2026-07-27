[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://localhost:8000',
    [string]$NodeId = 'node-linux-01',
    [ValidateRange(1, 60)]
    [int]$IntervalSeconds = 2,
    [switch]$Once
)

$BaseUrl = $BaseUrl.TrimEnd('/')

function Get-Json([string]$Path) {
    Invoke-RestMethod -Uri "$BaseUrl$Path" -TimeoutSec 10
}

do {
    try {
        $nodes = Invoke-RestMethod -Uri "$BaseUrl/api/v1/nodes" -TimeoutSec 10
        $node = $nodes | Where-Object { $_.id -eq $NodeId } | Select-Object -First 1
        $plans = Invoke-RestMethod -Uri "$BaseUrl/api/v1/provisioning?node_id=$NodeId" -TimeoutSec 10

        Clear-Host
        Write-Host "QuuDet node watcher  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
        Write-Host "Control: $BaseUrl   Node: $NodeId" -ForegroundColor DarkGray
        Write-Host ''

        if (-not $node) {
            Write-Host 'Node not found.' -ForegroundColor Red
        }
        else {
            $caps = $node.capabilities
            $color = if ($node.status -eq 'ONLINE') { 'Green' } else { 'Red' }
            Write-Host "Status: $($node.status)" -ForegroundColor $color
            Write-Host "Last seen: $($node.last_seen_at)   Running jobs: $($node.running_jobs)/$($node.max_concurrent_jobs)"
            Write-Host "GPU: $($caps.gpu_names -join ', ')   CUDA: $($caps.cuda_available)   VRAM(GB): $($caps.vram_gb -join ', ')"
            Write-Host "Cache: $($node.cache_root)   Free(GB): $([math]::Round(($node.cache_free_bytes / 1GB), 1))"
            $runtime = $caps.agent_runtime
            if ($runtime) {
                $runtimeColor = if ($runtime.active_job_id) { 'Yellow' } else { 'DarkGray' }
                Write-Host "Agent: $($runtime.phase)   Job: $($runtime.active_job_id)   PID: $($runtime.active_pid)" -ForegroundColor $runtimeColor
                Write-Host "Last output: $($runtime.last_output_at)   Exit: $($runtime.exit_code)" -ForegroundColor DarkGray
                if ($runtime.active_command) { Write-Host "Command: $($runtime.active_command)" -ForegroundColor DarkGray }
            }
        }

        Write-Host "`nProvisioning:" -ForegroundColor Yellow
        if (-not $plans) {
            Write-Host '  No provisioning plans.'
        }
        else {
            foreach ($plan in $plans | Select-Object -First 10) {
                $planColor = if ($plan.state -eq 'FAILED') { 'Red' } elseif ($plan.state -eq 'READY') { 'Green' } else { 'Yellow' }
                Write-Host "  [$($plan.state)] $($plan.id)  $($plan.download_progress)%  $($plan.updated_at)" -ForegroundColor $planColor
                if ($plan.error_message) {
                    Write-Host "    ERROR: $($plan.error_message)" -ForegroundColor Red
                }
            }
        }
    }
    catch {
        Write-Host "Watcher request failed: $($_.Exception.Message)" -ForegroundColor Red
    }

    if (-not $Once) { Start-Sleep -Seconds $IntervalSeconds }
} while (-not $Once)
