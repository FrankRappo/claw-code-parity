param(
    [Parameter(Mandatory)][string]$StateDir,
    [Parameter(Mandatory)][string]$StartScript,
    [ValidateRange(1, 300)][int]$RestartDelaySeconds = 3,
    [ValidateRange(0, 1000)][int]$MaxRestarts = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$statePath = Join-Path $StateDir 'watchdog.json'
$stopPath = Join-Path $StateDir 'watchdog.stop'
$restartCount = 0
$lastRestartAt = $null
$lastError = $null

function Save-WatchdogState {
    param([string]$State, [AllowNull()]$HostState)

    Write-ClawAtomicJson -Path $statePath -Value ([ordered]@{
        pid = $PID
        state = $State
        host_pid = if ($HostState -and $HostState.pid) { [int]$HostState.pid } else { $null }
        restart_count = $restartCount
        restart_delay_seconds = $RestartDelaySeconds
        max_restarts = $MaxRestarts
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
            Save-WatchdogState -State 'running' -HostState $hostState
            Start-Sleep -Milliseconds 500
            continue
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

        try {
            & $StartScript @parameters | Out-Null
        }
        catch {
            $lastError = $_.Exception.Message
            Save-WatchdogState -State 'retry_wait' -HostState $null
            continue
        }

        $hostState = Read-ClawJson -Path (Join-Path $StateDir 'host.json')
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
