Set-StrictMode -Version Latest

function Get-QuuDetSettingValue {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [string]$Backend,
        [Parameter(Mandatory)]
        [string]$DefaultValue
    )

    $environmentValue = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not [string]::IsNullOrWhiteSpace($environmentValue)) {
        return $environmentValue.Trim()
    }

    $envFile = Join-Path $Backend ".env"
    if (Test-Path -LiteralPath $envFile) {
        $pattern = "^\s*" + [regex]::Escape($Name) + "\s*=\s*(?<value>.*)\s*$"
        foreach ($line in Get-Content -LiteralPath $envFile) {
            $match = [regex]::Match($line, $pattern)
            if ($match.Success) {
                return $match.Groups["value"].Value.Trim().Trim("`"", "'")
            }
        }
    }

    return $DefaultValue
}

function ConvertTo-QuuDetEndpoint {
    param(
        [Parameter(Mandatory)]
        [string]$Url,
        [Parameter(Mandatory)]
        [string]$Label,
        [Parameter(Mandatory)]
        [int]$DefaultPort
    )

    if ($Url -match "^sqlite:") {
        return [pscustomobject]@{
            Label = $Label
            Url = $Url
            IsLocalFile = $true
            Host = $null
            Port = $null
        }
    }

    $normalisedUrl = $Url -replace "^([a-zA-Z][a-zA-Z0-9+.-]*)\+[a-zA-Z0-9+.-]+://", '$1://'
    try {
        $uri = [Uri]$normalisedUrl
    }
    catch {
        throw "Invalid $Label URL: $Url"
    }
    if ([string]::IsNullOrWhiteSpace($uri.Host)) {
        throw "Invalid $Label URL (host is missing): $Url"
    }

    return [pscustomobject]@{
        Label = $Label
        Url = $Url
        IsLocalFile = $false
        Host = $uri.Host
        Port = if ($uri.IsDefaultPort) { $DefaultPort } else { $uri.Port }
    }
}

function Test-QuuDetTcpEndpoint {
    param(
        [Parameter(Mandatory)]
        [string]$ServerHost,
        [Parameter(Mandatory)]
        [int]$Port,
        [ValidateRange(1, 30)]
        [int]$TimeoutSeconds = 2
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $client.BeginConnect($ServerHost, $Port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne([TimeSpan]::FromSeconds($TimeoutSeconds))) {
            return $false
        }
        $client.EndConnect($connect)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Test-QuuDetRuntimeDependencies {
    param(
        [Parameter(Mandatory)]
        [string]$Backend
    )

    $databaseUrl = Get-QuuDetSettingValue -Name "DATABASE_URL" -Backend $Backend -DefaultValue "sqlite:///./data/quudet.db"
    $redisUrl = Get-QuuDetSettingValue -Name "REDIS_URL" -Backend $Backend -DefaultValue "redis://localhost:6379/0"
    $database = ConvertTo-QuuDetEndpoint -Url $databaseUrl -Label "database" -DefaultPort 5432
    $redis = ConvertTo-QuuDetEndpoint -Url $redisUrl -Label "Redis" -DefaultPort 6379

    $databaseReady = $database.IsLocalFile -or (Test-QuuDetTcpEndpoint -ServerHost $database.Host -Port $database.Port)
    $redisReady = Test-QuuDetTcpEndpoint -ServerHost $redis.Host -Port $redis.Port

    return [pscustomobject]@{
        Ready = $databaseReady -and $redisReady
        Database = $database
        Redis = $redis
        DatabaseReady = $databaseReady
        RedisReady = $redisReady
    }
}

function Wait-QuuDetRuntimeDependencies {
    param(
        [Parameter(Mandatory)]
        [string]$Backend,
        [ValidateRange(1, 600)]
        [int]$TimeoutSeconds = 60,
        [ValidateRange(1, 30)]
        [int]$PollSeconds = 2,
        [scriptblock]$OnStatus = {}
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $status = Test-QuuDetRuntimeDependencies -Backend $Backend
        if ($status.Ready) {
            & $OnStatus "Runtime dependencies are ready."
            return $true
        }

        $missing = @()
        if (-not $status.DatabaseReady) {
            $missing += "$($status.Database.Label) at $($status.Database.Host):$($status.Database.Port)"
        }
        if (-not $status.RedisReady) {
            $missing += "Redis at $($status.Redis.Host):$($status.Redis.Port)"
        }
        & $OnStatus "Waiting for runtime dependencies: $($missing -join ', ')."
        Start-Sleep -Seconds $PollSeconds
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Test-QuuDetApiReady {
    param(
        [Parameter(Mandatory)]
        [string]$ApiBase,
        [ValidateRange(1, 30)]
        [int]$TimeoutSeconds = 3
    )

    $readyUrl = "$($ApiBase.TrimEnd('/'))/readyz"
    try {
        $request = [System.Net.HttpWebRequest]::Create($readyUrl)
        $request.Method = "GET"
        $request.Timeout = $TimeoutSeconds * 1000
        $request.ReadWriteTimeout = $TimeoutSeconds * 1000
        $response = $request.GetResponse()
        try {
            $reader = [System.IO.StreamReader]::new($response.GetResponseStream())
            try {
                $body = $reader.ReadToEnd() | ConvertFrom-Json
                return $body.status -eq "ready"
            }
            finally {
                $reader.Dispose()
            }
        }
        finally {
            $response.Dispose()
        }
    }
    catch {
        return $false
    }
}

function Wait-QuuDetApiReady {
    param(
        [Parameter(Mandatory)]
        [string]$ApiBase,
        [ValidateRange(1, 600)]
        [int]$TimeoutSeconds = 90,
        [ValidateRange(1, 30)]
        [int]$PollSeconds = 2,
        [scriptblock]$OnStatus = {}
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-QuuDetApiReady -ApiBase $ApiBase) {
            & $OnStatus "API readiness check passed: $($ApiBase.TrimEnd('/'))/readyz"
            return $true
        }
        & $OnStatus "Waiting for API readiness: $($ApiBase.TrimEnd('/'))/readyz"
        Start-Sleep -Seconds $PollSeconds
    } while ((Get-Date) -lt $deadline)

    return $false
}
