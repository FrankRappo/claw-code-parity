param(
    [Parameter(Mandatory)][string]$StateDir,
    [Parameter(Mandatory)][string]$StartScript,
    [ValidateRange(1, 300)][int]$RestartDelaySeconds = 3,
    [ValidateRange(0, 1000)][int]$MaxRestarts = 5,
    [string]$HealthUrl,
    [ValidateRange(1, 300)][int]$HealthProbeIntervalSeconds = 5,
    [ValidateRange(1, 100)][int]$HealthFailureThreshold = 3,
    [ValidateRange(1, 60)][int]$HealthTimeoutSeconds = 3,
    [ValidateRange(0, 600)][int]$HealthStartupGraceSeconds = 45
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$statePath = Join-Path $StateDir 'watchdog.json'
$stopPath = Join-Path $StateDir 'watchdog.stop'
$restartCount = 0
$lastRestartAt = $null
$lastError = $null
$healthConsecutiveFailures = 0
$lastHealthStatus = if ([string]::IsNullOrWhiteSpace($HealthUrl)) { 'disabled' } else { 'pending' }
$lastHealthRecoverable = $null
$lastHealthCheckAt = $null
$nextHealthCheckAt = (Get-Date).AddSeconds($HealthStartupGraceSeconds)
$healthBlocked = $false

if (-not [string]::IsNullOrWhiteSpace($HealthUrl)) {
    $healthUri = $null
    if (-not [uri]::TryCreate($HealthUrl, [System.UriKind]::Absolute, [ref]$healthUri) -or
            $healthUri.Scheme -notin @('http', 'https') -or
            -not $healthUri.IsLoopback) {
        throw 'HealthUrl must be an absolute loopback HTTP(S) URL.'
    }
}

function Get-WatchdogHealthResult {
    $script:lastHealthCheckAt = (Get-Date).ToUniversalTime().ToString('o')
    $response = $null
    try {
        $request = [System.Net.HttpWebRequest]::Create($HealthUrl)
        $request.Method = 'GET'
        $request.Timeout = $HealthTimeoutSeconds * 1000
        $request.ReadWriteTimeout = $HealthTimeoutSeconds * 1000
        try {
            $response = $request.GetResponse()
        }
        catch [System.Net.WebException] {
            if ($_.Exception.Response) {
                $response = $_.Exception.Response
            } else {
                $script:lastHealthStatus = ConvertTo-ClawSingleLine -Text $_.Exception.Message
                $script:lastHealthRecoverable = $true
                return [pscustomobject]@{ Ready = $false; Recoverable = $true }
            }
        }

        $statusCode = [int]$response.StatusCode
        $reader = [System.IO.StreamReader]::new($response.GetResponseStream())
        try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
        try { $payload = $body | ConvertFrom-Json } catch { $payload = $null }
        $propertyNames = if ($payload) {
            @($payload.PSObject.Properties | ForEach-Object { $_.Name })
        } else { @() }
        if (-not $payload -or $propertyNames -notcontains 'ready') {
            $script:lastHealthStatus = "invalid readiness response (HTTP $statusCode)"
            $script:lastHealthRecoverable = $false
            return [pscustomobject]@{ Ready = $false; Recoverable = $false }
        }
        if ($statusCode -ge 200 -and $statusCode -lt 300 -and $payload.ready -eq $true) {
            $script:lastHealthStatus = 'ready'
            $script:lastHealthRecoverable = $true
            return [pscustomobject]@{ Ready = $true; Recoverable = $true }
        }
        if ($propertyNames -contains 'auth' -and $payload.auth -eq $false) {
            $script:lastHealthStatus = 'authentication unavailable; manual login required'
            $script:lastHealthRecoverable = $false
            return [pscustomobject]@{ Ready = $false; Recoverable = $false }
        }
        if ($propertyNames -contains 'stalled' -and $payload.stalled -eq $true) {
            $script:lastHealthStatus = 'gateway request stalled'
            $script:lastHealthRecoverable = $true
            return [pscustomobject]@{ Ready = $false; Recoverable = $true }
        }
        if ($propertyNames -contains 'transport' -and $payload.transport -eq $false) {
            $script:lastHealthStatus = 'gateway transport unavailable'
            $script:lastHealthRecoverable = $true
            return [pscustomobject]@{ Ready = $false; Recoverable = $true }
        }
        $script:lastHealthStatus = "unclassified readiness failure (HTTP $statusCode)"
        $script:lastHealthRecoverable = $false
        return [pscustomobject]@{ Ready = $false; Recoverable = $false }
    }
    catch {
        $script:lastHealthStatus = ConvertTo-ClawSingleLine -Text $_.Exception.Message
        $script:lastHealthRecoverable = $true
        return [pscustomobject]@{ Ready = $false; Recoverable = $true }
    }
    finally {
        if ($response) { $response.Dispose() }
    }
}

function Save-WatchdogState {
    param([string]$State, [AllowNull()]$HostState)

    Write-ClawAtomicJson -Path $statePath -Value ([ordered]@{
        pid = $PID
        state = $State
        host_pid = if ($HostState -and $HostState.pid) { [int]$HostState.pid } else { $null }
        restart_count = $restartCount
        restart_delay_seconds = $RestartDelaySeconds
        max_restarts = $MaxRestarts
        health_url = if ([string]::IsNullOrWhiteSpace($HealthUrl)) { $null } else { $HealthUrl }
        health_probe_interval_seconds = $HealthProbeIntervalSeconds
        health_failure_threshold = $HealthFailureThreshold
        health_timeout_seconds = $HealthTimeoutSeconds
        health_startup_grace_seconds = $HealthStartupGraceSeconds
        health_consecutive_failures = $healthConsecutiveFailures
        health_recoverable = $lastHealthRecoverable
        last_health_status = $lastHealthStatus
        last_health_check_at = $lastHealthCheckAt
        last_restart_at = $lastRestartAt
        last_error = $lastError
        updated_at = (Get-Date).ToUniversalTime().ToString('o')
    })
}

try {
    $hostState = Read-ClawJson -Path (Join-Path $StateDir 'host.json')
    Save-WatchdogState -State 'running' -HostState $hostState

    while (-not (Test-Path -LiteralPath $stopPath)) {
        $hostState = Read-ClawJson -Path (Join-Path $StateDir 'host.json')
        if ($hostState -and (Test-ClawProcess -ProcessId $hostState.pid)) {
            $healthFailed = $false
            if (-not [string]::IsNullOrWhiteSpace($HealthUrl) -and (Get-Date) -ge $nextHealthCheckAt) {
                $healthResult = Get-WatchdogHealthResult
                if ($healthResult.Ready) {
                    $healthConsecutiveFailures = 0
                    $lastError = $null
                    $healthBlocked = $false
                } elseif (-not $healthResult.Recoverable) {
                    $healthConsecutiveFailures = 0
                    $lastError = $lastHealthStatus
                    $healthBlocked = $true
                    $nextHealthCheckAt = (Get-Date).AddSeconds($HealthProbeIntervalSeconds)
                    Save-WatchdogState -State 'health_blocked' -HostState $hostState
                    Start-Sleep -Milliseconds 500
                    continue
                } else {
                    $healthBlocked = $false
                    $healthConsecutiveFailures++
                }
                $nextHealthCheckAt = (Get-Date).AddSeconds($HealthProbeIntervalSeconds)
                $healthFailed = $healthConsecutiveFailures -ge $HealthFailureThreshold
            }
            if ($healthFailed) {
                $lastError = "Health check failed $healthConsecutiveFailures consecutive times: $lastHealthStatus"
                Save-WatchdogState -State 'unhealthy' -HostState $hostState
                if (Test-Path -LiteralPath $stopPath) { break }
                & taskkill.exe /PID ([int]$hostState.pid) /T /F 2>$null | Out-Null
                $deadline = (Get-Date).AddSeconds(10)
                while ((Test-ClawProcess -ProcessId $hostState.pid) -and (Get-Date) -lt $deadline) {
                    Start-Sleep -Milliseconds 100
                }
                if (Test-ClawProcess -ProcessId $hostState.pid) {
                    Stop-Process -Id ([int]$hostState.pid) -Force -ErrorAction SilentlyContinue
                }
            } else {
                $currentState = if ($healthBlocked) { 'health_blocked' } else { 'running' }
                Save-WatchdogState -State $currentState -HostState $hostState
                Start-Sleep -Milliseconds 500
                continue
            }
        }

        if ($MaxRestarts -gt 0 -and $restartCount -ge $MaxRestarts) {
            $lastError = "Maximum restart count reached: $MaxRestarts"
            Save-WatchdogState -State 'failed' -HostState $hostState
            exit 1
        }

        Start-Sleep -Seconds $RestartDelaySeconds
        if (Test-Path -LiteralPath $stopPath) { break }

        # Re-check after the delay in case an operator already recovered it.
        $hostState = Read-ClawJson -Path (Join-Path $StateDir 'host.json')
        if ($hostState -and (Test-ClawProcess -ProcessId $hostState.pid)) { continue }

        $config = Get-ClawControlConfig -StateDir $StateDir
        $restartCount++
        $lastRestartAt = (Get-Date).ToUniversalTime().ToString('o')
        $lastError = $null
        Save-WatchdogState -State 'restarting' -HostState $hostState

        $parameters = @{
            Name = [string]$config.name
            LauncherPath = [string]$config.launcher_path
            WorkspacePath = [string]$config.workspace_path
            Resume = 'latest'
            AgentMode = if ($config.agent_mode) { [string]$config.agent_mode } else { 'on' }
            IdlePromptRegex = [string]$config.idle_prompt_regex
            InitialMessage = 'Continue the current unfinished task after automatic process recovery. Use the saved context from this session and do not start over.'
        }
        if ($config.model) { $parameters.Model = [string]$config.model }
        if (-not [string]::IsNullOrWhiteSpace($HealthUrl)) {
            $parameters.WatchdogHealthUrl = $HealthUrl
            $parameters.WatchdogHealthProbeIntervalSeconds = $HealthProbeIntervalSeconds
            $parameters.WatchdogHealthFailureThreshold = $HealthFailureThreshold
            $parameters.WatchdogHealthTimeoutSeconds = $HealthTimeoutSeconds
            $parameters.WatchdogHealthStartupGraceSeconds = $HealthStartupGraceSeconds
        }

        try {
            & $StartScript @parameters | Out-Null
        }
        catch {
            $lastError = $_.Exception.Message
            Save-WatchdogState -State 'retry_wait' -HostState $null
            continue
        }

        $hostState = Read-ClawJson -Path (Join-Path $StateDir 'host.json')
        $healthConsecutiveFailures = 0
        $lastHealthStatus = if ([string]::IsNullOrWhiteSpace($HealthUrl)) { 'disabled' } else { 'pending' }
        $lastHealthRecoverable = $null
        $healthBlocked = $false
        $nextHealthCheckAt = (Get-Date).AddSeconds($HealthStartupGraceSeconds)
        Save-WatchdogState -State 'running' -HostState $hostState
    }

    $hostState = Read-ClawJson -Path (Join-Path $StateDir 'host.json')
    Save-WatchdogState -State 'stopped' -HostState $hostState
}
catch {
    $lastError = $_.Exception.Message
    Save-WatchdogState -State 'failed' -HostState $null
    throw
}
