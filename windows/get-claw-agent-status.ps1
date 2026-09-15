param(
    [string]$Name = 'kimi',
    [string]$WorkspacePath = (Get-Location).Path,
    [int]$TailLines = 20
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'agent-control\common.ps1')
$stateDir = Get-ClawControlStateDir -WorkspacePath $WorkspacePath -Name $Name
$config = Get-ClawControlConfig -StateDir $stateDir
$hostState = Read-ClawJson -Path (Join-Path $stateDir 'host.json')
$dispatcherState = Read-ClawJson -Path (Join-Path $stateDir 'dispatcher.json')
$heartbeat = Read-ClawJson -Path (Join-Path $stateDir 'heartbeat.json')
$watchdogState = Read-ClawJson -Path (Join-Path $stateDir 'watchdog.json')
$snapshot = Read-ClawJson -Path (Join-Path $stateDir 'screen.json')
$turnState = Read-ClawJson -Path (Join-Path $stateDir 'turn-state.json')
$activeTask = Read-ClawJson -Path (Join-Path $stateDir 'active-task.json')
$tail = ''
$screenPath = Join-Path $stateDir 'screen.txt'
if (Test-Path -LiteralPath $screenPath) {
    try {
        $tail = (Get-Content -LiteralPath $screenPath -Encoding utf8 | Select-Object -Last $TailLines) -join [Environment]::NewLine
    } catch [System.Management.Automation.ItemNotFoundException] {
        # The dispatcher replaces the snapshot atomically; a status read may
        # race the short interval between the old file removal and rename.
        $tail = ''
    }
}

$queued = @(Get-ChildItem -LiteralPath (Join-Path $stateDir 'queue') -Filter '*.json' -File -ErrorAction SilentlyContinue)
$processing = @(Get-ChildItem -LiteralPath (Join-Path $stateDir 'processing') -Filter '*.json' -File -ErrorAction SilentlyContinue)
$delivered = @(Get-ChildItem -LiteralPath (Join-Path $stateDir 'delivered') -Filter '*.json' -File -ErrorAction SilentlyContinue)
$failed = @(Get-ChildItem -LiteralPath (Join-Path $stateDir 'failed') -Filter '*.json' -File -ErrorAction SilentlyContinue)
$acks = @(Get-ChildItem -LiteralPath (Join-Path $stateDir 'acks') -Filter '*.txt' -File -ErrorAction SilentlyContinue)

[pscustomobject]@{
    name = $Name
    running = ($hostState -and (Test-ClawProcess -ProcessId $hostState.pid))
    host = $hostState
    dispatcher_running = ($dispatcherState -and (Test-ClawProcess -ProcessId $dispatcherState.pid))
    dispatcher = $dispatcherState
    watchdog_running = ($watchdogState -and (Test-ClawProcess -ProcessId $watchdogState.pid))
    watchdog = $watchdogState
    heartbeat = $heartbeat
    current_line = if ($snapshot) { $snapshot.current_line } else { $null }
    turn = $turnState
    auto_continue_task = $activeTask
    queue_count = $queued.Count
    processing_count = $processing.Count
    submitted_count = $delivered.Count
    failed_count = $failed.Count
    acknowledged_count = $acks.Count
    state_dir = $stateDir
    screen_tail = $tail
} | ConvertTo-Json -Depth 8
