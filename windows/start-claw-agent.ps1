param(
    [string]$Name = 'kimi',
    [string]$LauncherPath = (Join-Path (Split-Path $PSScriptRoot -Parent) 'claw-kimi.cmd'),
    [string]$WorkspacePath = (Get-Location).Path,
    [string]$Resume = 'latest',
    [string]$Model,
    [ValidateSet('on', 'off', 'keep')][string]$AgentMode = 'on',
    [string]$InitialMessage,
    [string]$IdlePromptRegex = '^\s*>\s*$',
    [switch]$AutoContinue,
    [ValidateRange(1, 300)][int]$AutoContinueDelaySeconds = 5,
    [ValidateRange(1, 1000)][int]$AutoContinueMaxTurns = 50,
    [switch]$Watchdog,
    [ValidateRange(1, 300)][int]$WatchdogRestartDelaySeconds = 3,
    [ValidateRange(0, 1000)][int]$WatchdogMaxRestarts = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$internal = Join-Path $PSScriptRoot 'agent-control'
. (Join-Path $internal 'common.ps1')
Add-Type -Path (Join-Path $internal 'ClawAgentConsole.cs')

$launcher = (Resolve-Path -LiteralPath $LauncherPath).Path
$workspace = (Resolve-Path -LiteralPath $WorkspacePath).Path
$stateDir = Get-ClawControlStateDir -WorkspacePath $workspace -Name $Name
Initialize-ClawControlState -StateDir $stateDir

$resumeTarget = $Resume
$resumeSessionPath = $null
if ($Resume -eq 'latest') {
    $sessionDir = Join-Path $workspace '.claw\sessions'
    $resumeTarget = 'none'
    foreach ($candidate in @(Get-ChildItem -LiteralPath $sessionDir -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in @('.jsonl', '.json') } |
            Sort-Object LastWriteTime -Descending)) {
        # A freshly opened REPL immediately creates a metadata-only session.
        # Resolve `latest` before launching and skip those empty files, or the
        # subsequent `/resume latest` would accidentally resume itself.
        if (@(Get-Content -LiteralPath $candidate.FullName -TotalCount 2 -Encoding utf8).Count -ge 2) {
            $resumeTarget = $candidate.BaseName
            $resumeSessionPath = $candidate.FullName
            break
        }
    }
} elseif ($Resume -ne 'none') {
    $sessionDir = Join-Path $workspace '.claw\sessions'
    foreach ($extension in @('.jsonl', '.json')) {
        $candidatePath = Join-Path $sessionDir ($Resume + $extension)
        if (Test-Path -LiteralPath $candidatePath) {
            $resumeSessionPath = (Resolve-Path -LiteralPath $candidatePath).Path
            break
        }
    }
}

$modelSource = 'launcher_default'
if (-not [string]::IsNullOrWhiteSpace($Model)) {
    $modelSource = 'explicit'
} elseif ($resumeSessionPath) {
    try {
        $firstRecord = Get-Content -LiteralPath $resumeSessionPath -TotalCount 1 -Encoding utf8 | ConvertFrom-Json
        if ($firstRecord.model -and -not [string]::IsNullOrWhiteSpace([string]$firstRecord.model)) {
            $Model = [string]$firstRecord.model
            $modelSource = 'session_metadata'
        }
    } catch {
        # Legacy JSON sessions and old JSONL metadata have no model route.
        # Their caller can supply -Model once; Claw will persist it thereafter.
    }
}

$oldHost = Read-ClawJson -Path (Join-Path $stateDir 'host.json')
if ($oldHost -and (Test-ClawProcess -ProcessId $oldHost.pid)) {
    throw "Controlled Claw session '$Name' is already running as PID $($oldHost.pid)."
}
$oldDispatcher = Read-ClawJson -Path (Join-Path $stateDir 'dispatcher.json')
if ($oldDispatcher -and (Test-ClawProcess -ProcessId $oldDispatcher.pid)) {
    Stop-Process -Id ([int]$oldDispatcher.pid) -Force
}

# Raw console commands (especially a stale /exit) must never cross a host
# restart. Preserve undelivered instruction files, but archive stale control
# keystrokes so a recovered session cannot close itself unexpectedly.
foreach ($folder in @('queue', 'processing')) {
    foreach ($file in @(Get-ChildItem -LiteralPath (Join-Path $stateDir $folder) -Filter '*.json' -File -ErrorAction SilentlyContinue)) {
        try {
            $pending = Read-ClawJson -Path $file.FullName
            if ($pending.kind -ne 'instruction') {
                $pending.status = 'stale_after_host_restart'
                $pending | Add-Member -NotePropertyName archived_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
                Write-ClawAtomicJson -Path $file.FullName -Value $pending
                Move-Item -LiteralPath $file.FullName -Destination (Join-Path $stateDir "failed\$($file.Name)") -Force
            } elseif ($folder -eq 'processing') {
                Move-Item -LiteralPath $file.FullName -Destination (Join-Path $stateDir "queue\$($file.Name)") -Force
            }
        } catch {}
    }
}

Remove-Item -LiteralPath (Join-Path $stateDir 'dispatcher.stop') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $stateDir 'interrupt.signal') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $stateDir 'host.json') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $stateDir 'dispatcher.json') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $stateDir 'heartbeat.json') -Force -ErrorAction SilentlyContinue
$config = [ordered]@{
    schema_version = 1
    name = $Name
    state_dir = $stateDir
    launcher_path = $launcher
    workspace_path = $workspace
    resume_requested = $Resume
    resume_target = $resumeTarget
    resume_session_path = $resumeSessionPath
    model = if ([string]::IsNullOrWhiteSpace($Model)) { $null } else { $Model }
    model_source = $modelSource
    agent_mode = $AgentMode
    idle_prompt_regex = $IdlePromptRegex
    idle_cursor_x = 2
    idle_timeout_seconds = 86400
    interrupt_timeout_seconds = 60
    auto_continue = [bool]$AutoContinue
    auto_continue_delay_seconds = $AutoContinueDelaySeconds
    auto_continue_max_turns = $AutoContinueMaxTurns
    started_at = (Get-Date).ToUniversalTime().ToString('o')
}
Write-ClawAtomicJson -Path (Join-Path $stateDir 'config.json') -Value $config

$powerShell = (Get-Command powershell.exe).Source
$hostScript = Join-Path $internal 'host.ps1'
$hostArguments = @(
    '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', "`"$hostScript`"",
    '-StateDir', "`"$stateDir`"",
    '-LauncherPath', "`"$launcher`"",
    '-WorkspacePath', "`"$workspace`"",
    '-Resume', "`"$resumeTarget`""
)
if (-not [string]::IsNullOrWhiteSpace($Model)) {
    $hostArguments += @('-Model', "`"$Model`"")
}
$hostArguments = $hostArguments -join ' '
$createdProcessId = [ClawAgentControl.NativeConsole]::StartInNewConsole($powerShell, $hostArguments, $workspace)

$hostDeadline = (Get-Date).AddSeconds(20)
do {
    Start-Sleep -Milliseconds 100
    $hostState = Read-ClawJson -Path (Join-Path $stateDir 'host.json')
    if ($hostState -and $hostState.state -eq 'running' -and [int]$hostState.pid -eq $createdProcessId) { break }
    if (-not (Test-ClawProcess -ProcessId $createdProcessId)) { throw 'The Claw host exited during startup.' }
} while ((Get-Date) -lt $hostDeadline)
if (-not $hostState -or $hostState.state -ne 'running' -or [int]$hostState.pid -ne $createdProcessId) { throw 'Timed out waiting for the visible Claw host.' }

$dispatcherScript = Join-Path $internal 'dispatcher.ps1'
$dispatcherArguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$dispatcherScript`" -StateDir `"$stateDir`""
$dispatcherProcess = Start-Process $powerShell -ArgumentList $dispatcherArguments -WindowStyle Hidden -PassThru
$dispatcherDeadline = (Get-Date).AddSeconds(20)
do {
    Start-Sleep -Milliseconds 100
    $dispatcherState = Read-ClawJson -Path (Join-Path $stateDir 'dispatcher.json')
    if ($dispatcherState -and $dispatcherState.state -eq 'running' -and [int]$dispatcherState.pid -eq $dispatcherProcess.Id) { break }
    if ($dispatcherProcess.HasExited) { throw 'The Claw dispatcher exited during startup.' }
} while ((Get-Date) -lt $dispatcherDeadline)
if (-not $dispatcherState -or $dispatcherState.state -ne 'running' -or [int]$dispatcherState.pid -ne $dispatcherProcess.Id) { throw 'Timed out waiting for the Claw dispatcher.' }

if ($resumeTarget -ne 'none') {
    New-ClawControlRequest -StateDir $stateDir -Kind raw -Message "/resume $resumeTarget" -Mode next | Out-Null
}
if ($AgentMode -ne 'keep') {
    New-ClawControlRequest -StateDir $stateDir -Kind raw -Message "/agent $AgentMode" -Mode next | Out-Null
}
if (-not [string]::IsNullOrWhiteSpace($InitialMessage)) {
    New-ClawControlRequest -StateDir $stateDir -Kind instruction -Message $InitialMessage -Mode next | Out-Null
}

$watchdogState = $null
if ($Watchdog) {
    $watchdogState = & (Join-Path $PSScriptRoot 'start-claw-agent-watchdog.ps1') `
        -Name $Name `
        -WorkspacePath $workspace `
        -RestartDelaySeconds $WatchdogRestartDelaySeconds `
        -MaxRestarts $WatchdogMaxRestarts | ConvertFrom-Json
}

[pscustomobject]@{
    name = $Name
    state = 'running'
    host_pid = [int]$hostState.pid
    dispatcher_pid = [int]$dispatcherState.pid
    state_dir = $stateDir
    resume_requested = $Resume
    resume_target = $resumeTarget
    model = if ([string]::IsNullOrWhiteSpace($Model)) { $null } else { $Model }
    model_source = $modelSource
    agent_mode = $AgentMode
    auto_continue = [bool]$AutoContinue
    watchdog_pid = if ($watchdogState) { [int]$watchdogState.watchdog_pid } else { $null }
} | ConvertTo-Json -Depth 4
