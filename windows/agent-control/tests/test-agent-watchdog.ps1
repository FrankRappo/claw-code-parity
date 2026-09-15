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
        -WatchdogMaxRestarts 2 | ConvertFrom-Json
    if (-not $started.watchdog_pid) { throw 'Watchdog PID was not returned.' }

    $deadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 200
        $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
    } while ((-not $status.watchdog_running -or $status.submitted_count -lt 2) -and (Get-Date) -lt $deadline)
    if (-not $status.watchdog_running) { throw 'Watchdog did not become healthy.' }

    $firstHostPid = [int]$status.host.pid
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
            'unexpected host termination triggered one restart',
            'replacement host resumed the latest durable session',
            'replacement host received an explicit continuation instruction',
            'intentional stop disabled recovery before terminating the host'
        )
    } | ConvertTo-Json -Depth 4
}
finally {
    try { & (Join-Path $repo 'windows\stop-claw-agent.ps1') -Name $name -WorkspacePath $workspace -TimeoutSeconds 2 -Force | Out-Null } catch {}
    Remove-Item -LiteralPath $workspace -Recurse -Force -ErrorAction SilentlyContinue
}
