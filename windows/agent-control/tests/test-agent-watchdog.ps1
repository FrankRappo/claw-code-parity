$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$workspace = Join-Path $env:TEMP ('claw-agent-watchdog-test-' + [guid]::NewGuid().ToString('N'))
$log = Join-Path $workspace 'received.log'
$envLog = Join-Path $workspace 'environment.log'
$name = 'watchdog-integration'
$sessionId = 'session-watchdog-route'
$sessionModel = 'kimi-k2d6-watchdog-route'
$sessionDir = Join-Path $workspace '.claw\sessions'
$healthFailureFile = Join-Path $workspace 'health-failures.txt'
$healthStopFile = Join-Path $workspace 'health-server.stop'
$healthListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$healthListener.Start()
$healthPort = ([System.Net.IPEndPoint]$healthListener.LocalEndpoint).Port
$healthListener.Stop()
$healthUrl = "http://127.0.0.1:$healthPort/ready"
$healthJob = Start-Job -ArgumentList $healthPort, $healthFailureFile, $healthStopFile -ScriptBlock {
    param($Port, $FailureFile, $StopFile)
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, [int]$Port)
    $listener.Start()
    try {
        while (-not (Test-Path -LiteralPath $StopFile)) {
            if (-not $listener.Pending()) { Start-Sleep -Milliseconds 50; continue }
            $client = $listener.AcceptTcpClient()
            try {
                $stream = $client.GetStream()
                $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::ASCII, $false, 1024, $true)
                while (($line = $reader.ReadLine()) -ne '') { if ($null -eq $line) { break } }
                $mode = ''
                if (Test-Path -LiteralPath $FailureFile) {
                    $mode = (Get-Content -LiteralPath $FailureFile -Raw).Trim()
                }
                $remaining = 0
                [void][int]::TryParse($mode, [ref]$remaining)
                if ($mode -eq 'missing') {
                    $status = '200 OK'
                    $body = '{}'
                } elseif ($mode -eq 'auth') {
                    $status = '503 Service Unavailable'
                    $body = '{"ready":false,"transport":true,"auth":false,"stalled":false}'
                } elseif ($remaining -gt 0) {
                    [System.IO.File]::WriteAllText($FailureFile, [string]($remaining - 1))
                    $status = '503 Service Unavailable'
                    $body = '{"ready":false,"transport":true,"auth":true,"stalled":true}'
                } else {
                    $status = '200 OK'
                    $body = '{"ready":true}'
                }
                $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
                $headers = "HTTP/1.1 $status`r`nContent-Type: application/json`r`nContent-Length: $($bodyBytes.Length)`r`nConnection: close`r`n`r`n"
                $headerBytes = [System.Text.Encoding]::ASCII.GetBytes($headers)
                $stream.Write($headerBytes, 0, $headerBytes.Length)
                $stream.Write($bodyBytes, 0, $bodyBytes.Length)
                $stream.Flush()
            } finally { $client.Dispose() }
        }
    } finally { $listener.Stop() }
}
New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null
$env:FAKE_CLAW_LOG = $log
$env:FAKE_CLAW_ENV_LOG = $envLog

$sessionRecords = [string[]]@(
    '{"created_at_ms":1,"model":"' + $sessionModel + '","session_id":"' + $sessionId + '","type":"session_meta","updated_at_ms":1,"version":1}'
    '{"message":{"blocks":[{"text":"unfinished task","type":"text"}],"role":"user"},"type":"message"}'
)
[System.IO.File]::WriteAllLines(
    (Join-Path $sessionDir "$sessionId.jsonl"),
    $sessionRecords,
    [System.Text.UTF8Encoding]::new($false)
)

try {
    $started = & (Join-Path $repo 'windows\start-claw-agent.ps1') `
        -Name $name `
        -LauncherPath (Join-Path $PSScriptRoot 'fake-claw.cmd') `
        -WorkspacePath $workspace `
        -Resume latest `
        -AgentMode on `
        -Watchdog `
        -WatchdogRestartDelaySeconds 1 `
        -WatchdogMaxRestarts 2 `
        -WatchdogHealthUrl $healthUrl `
        -WatchdogHealthProbeIntervalSeconds 1 `
        -WatchdogHealthFailureThreshold 2 `
        -WatchdogHealthTimeoutSeconds 2 `
        -WatchdogHealthStartupGraceSeconds 4 | ConvertFrom-Json
    if (-not $started.watchdog_pid) { throw 'Watchdog PID was not returned.' }

    $deadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 200
        $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
    } while ((-not $status.watchdog_running -or $status.submitted_count -lt 2) -and (Get-Date) -lt $deadline)
    if (-not $status.watchdog_running) { throw 'Watchdog did not become healthy.' }

    $firstHostPid = [int]$status.host.pid

    # The host becomes visible before a provider launcher finishes. Health
    # failures during the configured startup grace must not kill that host.
    [System.IO.File]::WriteAllText($healthFailureFile, '2')
    Start-Sleep -Seconds 2
    $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
    if (-not $status.running -or [int]$status.host.pid -ne $firstHostPid) {
        throw 'Watchdog killed a host during health startup grace.'
    }
    if ((Get-Content -LiteralPath $healthFailureFile -Raw).Trim() -ne '2') {
        throw 'Watchdog probed health before startup grace elapsed.'
    }
    Remove-Item -LiteralPath $healthFailureFile -Force
    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 200
        $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
    } while (($status.watchdog.last_health_status -ne 'ready' -or [int]$status.watchdog.health_consecutive_failures -ne 0) -and (Get-Date) -lt $deadline)
    if ($status.watchdog.last_health_status -ne 'ready') { throw 'Initial health probe did not become ready.' }

    # Invalid schemas and explicit authentication failures are not recoverable
    # by restarting; they must be recorded without entering a restart storm.
    foreach ($blockedMode in @('missing', 'auth')) {
        $previousHealthCheckAt = $status.watchdog.last_health_check_at
        [System.IO.File]::WriteAllText($healthFailureFile, $blockedMode)
        $deadline = (Get-Date).AddSeconds(10)
        do {
            Start-Sleep -Milliseconds 200
            $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
        } while (($status.watchdog.state -ne 'health_blocked' -or
                $status.watchdog.last_health_check_at -eq $previousHealthCheckAt) -and
            (Get-Date) -lt $deadline)
        $blockedHostPid = if ($status.host -and $status.host.pid) { [int]$status.host.pid } else { 0 }
        if (-not $status.running -or $blockedHostPid -ne $firstHostPid -or [int]$status.watchdog.restart_count -ne 0) {
            throw "Non-recoverable health mode '$blockedMode' caused a restart. running=$($status.running) host=$blockedHostPid expected=$firstHostPid restarts=$($status.watchdog.restart_count) state=$($status.watchdog.state) health=$($status.watchdog.last_health_status) recoverable=$($status.watchdog.health_recoverable)"
        }
        if ($status.watchdog.state -ne 'health_blocked') {
            throw "Non-recoverable health mode '$blockedMode' was not recorded."
        }
        $expectedStatus = if ($blockedMode -eq 'auth') {
            'authentication unavailable; manual login required'
        } else { 'invalid readiness response (HTTP 200)' }
        if ($status.watchdog.last_health_status -ne $expectedStatus) {
            throw "Non-recoverable health mode '$blockedMode' was not classified explicitly."
        }
        Start-Sleep -Milliseconds 700
        $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
        if ($status.watchdog.state -ne 'health_blocked') {
            throw "Non-recoverable health mode '$blockedMode' was not persistent between probes."
        }
    }
    Remove-Item -LiteralPath $healthFailureFile -Force
    Start-Sleep -Seconds 2

    # One recoverable failure followed by success must reset the counter and
    # leave the current host untouched.
    [System.IO.File]::WriteAllText($healthFailureFile, '1')
    Start-Sleep -Seconds 3
    $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
    if ([int]$status.host.pid -ne $firstHostPid -or [int]$status.watchdog.restart_count -ne 0) {
        throw 'A transient readiness failure restarted the host.'
    }
    if ([int]$status.watchdog.health_consecutive_failures -ne 0) {
        throw 'A successful readiness probe did not reset the transient failure.'
    }

    & taskkill.exe /PID $firstHostPid /T /F | Out-Null

    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 250
        $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
        $newHostPid = if ($status.host -and $status.host.pid) { [int]$status.host.pid } else { 0 }
    } while ((-not $status.running -or $newHostPid -eq $firstHostPid -or [int]$status.watchdog.restart_count -lt 1) -and (Get-Date) -lt $deadline)

    if (-not $status.running -or $newHostPid -eq $firstHostPid) { throw 'Watchdog did not replace the failed host.' }
    if ([int]$status.watchdog.restart_count -ne 1) { throw 'Unexpected watchdog restart count.' }

    $deadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 200
        $lines = @(Get-Content -LiteralPath $log -Encoding utf8 -ErrorAction SilentlyContinue)
        $continuationStored = $false
        foreach ($messageFile in @(Get-ChildItem -LiteralPath (Join-Path $workspace ".claw\control\$name\messages") -Filter '*.md' -File -ErrorAction SilentlyContinue)) {
            if ((Get-Content -LiteralPath $messageFile.FullName -Raw -Encoding utf8) -match 'Continue the current unfinished task') {
                $continuationStored = $true
                break
            }
        }
    } while ((@($lines | Where-Object { $_ -eq "/resume $sessionId" }).Count -lt 2 -or -not $continuationStored) -and (Get-Date) -lt $deadline)
    if (@($lines | Where-Object { $_ -eq "/resume $sessionId" }).Count -lt 2) {
        throw "Recovered host did not resume the latest durable session. Lines=$($lines -join ' | ')"
    }
    if (-not $continuationStored) {
        throw 'Recovered host did not receive the continuation instruction.'
    }

    # A live host must also be replaced when its configured dependency remains
    # unready. The fake endpoint fails exactly twice, then becomes healthy so
    # the replacement can stabilize instead of exhausting the restart budget.
    [System.IO.File]::WriteAllText($healthFailureFile, '2')
    $healthFailedHostPid = $newHostPid
    Start-Sleep -Seconds 2
    $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
    if (-not $status.running -or [int]$status.host.pid -ne $healthFailedHostPid -or [int]$status.watchdog.restart_count -ne 1) {
        throw 'Replacement host was killed during its health startup grace.'
    }
    if ((Get-Content -LiteralPath $healthFailureFile -Raw).Trim() -ne '2') {
        throw 'Replacement health endpoint was probed before startup grace elapsed.'
    }
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 250
        $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
        $healthRecoveredPid = if ($status.host -and $status.host.pid) { [int]$status.host.pid } else { 0 }
    } while ((-not $status.running -or
            $healthRecoveredPid -eq $healthFailedHostPid -or
            [int]$status.watchdog.restart_count -lt 2 -or
            $status.watchdog.state -ne 'running' -or
            [int]$status.watchdog.health_consecutive_failures -ne 0) -and (Get-Date) -lt $deadline)
    if (-not $status.running -or $healthRecoveredPid -eq $healthFailedHostPid) {
        throw 'Watchdog did not replace a live host after repeated readiness failures.'
    }
    if ([int]$status.watchdog.restart_count -ne 2) { throw 'Health restart was not counted exactly once.' }
    if ([int]$status.watchdog.health_consecutive_failures -ne 0) { throw 'Health failure count did not recover.' }

    $stopped = & (Join-Path $repo 'windows\stop-claw-agent.ps1') `
        -Name $name -WorkspacePath $workspace -TimeoutSeconds 10 -Force | ConvertFrom-Json
    Start-Sleep -Seconds 2
    $statusAfterStop = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
    if ($statusAfterStop.running -or $statusAfterStop.watchdog_running) {
        throw 'An intentional stop was incorrectly restarted.'
    }
    if (-not $stopped.watchdog_stopped) { throw 'Stop command did not stop the watchdog.' }

    [pscustomobject]@{
        passed = $true
        assertions = @(
            'watchdog attached to the controlled Claw process',
            'startup grace protected both initial and replacement hosts',
            'invalid readiness schema and auth failure did not cause restart storms',
            'a transient readiness failure recovered without a restart',
            'unexpected host termination triggered one restart',
            'replacement host resumed the latest durable session',
            'replacement host received an explicit continuation instruction',
            'repeated readiness failures replaced a live but unhealthy host',
            'a transient health recovery reset the consecutive failure count',
            'intentional stop disabled recovery before terminating the host'
        )
    } | ConvertTo-Json -Depth 4
}
finally {
    try { & (Join-Path $repo 'windows\stop-claw-agent.ps1') -Name $name -WorkspacePath $workspace -TimeoutSeconds 2 -Force | Out-Null } catch {}
    New-Item -ItemType File -Path $healthStopFile -Force -ErrorAction SilentlyContinue | Out-Null
    Stop-Job -Job $healthJob -ErrorAction SilentlyContinue
    Remove-Job -Job $healthJob -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $workspace -Recurse -Force -ErrorAction SilentlyContinue
}
