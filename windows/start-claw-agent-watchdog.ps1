param(
    [string]$Name = 'kimi',
    [string]$WorkspacePath = (Get-Location).Path,
    [ValidateRange(1, 300)][int]$RestartDelaySeconds = 3,
    [ValidateRange(0, 1000)][int]$MaxRestarts = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$internal = Join-Path $PSScriptRoot 'agent-control'
. (Join-Path $internal 'common.ps1')

$workspace = (Resolve-Path -LiteralPath $WorkspacePath).Path
$stateDir = Get-ClawControlStateDir -WorkspacePath $workspace -Name $Name
$config = Get-ClawControlConfig -StateDir $stateDir
$hostState = Read-ClawJson -Path (Join-Path $stateDir 'host.json')
if (-not $hostState -or -not (Test-ClawProcess -ProcessId $hostState.pid)) {
    throw "Controlled Claw session '$Name' must be running before its watchdog can be attached."
}

$watchdogPath = Join-Path $stateDir 'watchdog.json'
$oldWatchdog = Read-ClawJson -Path $watchdogPath
if ($oldWatchdog -and (Test-ClawProcess -ProcessId $oldWatchdog.pid)) {
    [pscustomobject]@{
        name = $Name
        state = 'already_running'
        watchdog_pid = [int]$oldWatchdog.pid
        host_pid = [int]$hostState.pid
        state_dir = $stateDir
    } | ConvertTo-Json
    exit 0
}

Remove-Item -LiteralPath (Join-Path $stateDir 'watchdog.stop') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $watchdogPath -Force -ErrorAction SilentlyContinue

$powerShell = (Get-Command powershell.exe).Source
$watchdogScript = Join-Path $internal 'watchdog.ps1'
$startScript = Join-Path $PSScriptRoot 'start-claw-agent.ps1'
$stdoutLog = Join-Path $stateDir 'watchdog.stdout.log'
$stderrLog = Join-Path $stateDir 'watchdog.stderr.log'
Remove-Item -LiteralPath $stdoutLog, $stderrLog -Force -ErrorAction SilentlyContinue
$arguments = @(
    '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', "`"$watchdogScript`"",
    '-StateDir', "`"$stateDir`"",
    '-StartScript', "`"$startScript`"",
    '-RestartDelaySeconds', $RestartDelaySeconds,
    '-MaxRestarts', $MaxRestarts
) -join ' '
$process = Start-Process $powerShell -ArgumentList $arguments -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru

$deadline = (Get-Date).AddSeconds(10)
do {
    Start-Sleep -Milliseconds 100
    $watchdogState = Read-ClawJson -Path $watchdogPath
    if ($watchdogState -and $watchdogState.state -eq 'running' -and [int]$watchdogState.pid -eq $process.Id) { break }
    if ($process.HasExited) {
        $detail = if (Test-Path -LiteralPath $stderrLog) { (Get-Content -LiteralPath $stderrLog -Raw).Trim() } else { '' }
        throw "The Claw watchdog exited during startup. $detail"
    }
} while ((Get-Date) -lt $deadline)
if (-not $watchdogState -or $watchdogState.state -ne 'running' -or [int]$watchdogState.pid -ne $process.Id) {
    throw 'Timed out waiting for the Claw watchdog.'
}

[pscustomobject]@{
    name = $Name
    state = 'running'
    watchdog_pid = [int]$process.Id
    host_pid = [int]$hostState.pid
    state_dir = $stateDir
    restart_delay_seconds = $RestartDelaySeconds
    max_restarts = $MaxRestarts
} | ConvertTo-Json
