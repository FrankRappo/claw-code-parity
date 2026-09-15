param(
    [string]$Name = 'kimi',
    [string]$WorkspacePath = (Get-Location).Path,
    [int]$TimeoutSeconds = 20,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'agent-control\common.ps1')
$stateDir = Get-ClawControlStateDir -WorkspacePath $WorkspacePath -Name $Name
$config = Get-ClawControlConfig -StateDir $stateDir
$hostState = Read-ClawJson -Path (Join-Path $stateDir 'host.json')
$dispatcherState = Read-ClawJson -Path (Join-Path $stateDir 'dispatcher.json')
$wasForced = $false

if ($hostState -and (Test-ClawProcess -ProcessId $hostState.pid)) {
    New-ClawControlRequest -StateDir $stateDir -Kind raw -Message '/exit' -Mode now | Out-Null
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Test-ClawProcess -ProcessId $hostState.pid) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 200
    }
    if ((Test-ClawProcess -ProcessId $hostState.pid) -and $Force) {
        & taskkill.exe /PID ([int]$hostState.pid) /T /F | Out-Null
        $wasForced = $true
    }
}

Write-ClawAtomicText -Path (Join-Path $stateDir 'dispatcher.stop') -Content ((Get-Date).ToUniversalTime().ToString('o'))
if ($dispatcherState -and (Test-ClawProcess -ProcessId $dispatcherState.pid)) {
    $deadline = (Get-Date).AddSeconds(5)
    while ((Test-ClawProcess -ProcessId $dispatcherState.pid) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (Test-ClawProcess -ProcessId $dispatcherState.pid) { Stop-Process -Id ([int]$dispatcherState.pid) -Force }
}

$stillRunning = $hostState -and (Test-ClawProcess -ProcessId $hostState.pid)
[pscustomobject]@{
    name = $Name
    stopped = -not $stillRunning
    forced = $wasForced
    note = if ($stillRunning) {
        'Claw did not exit gracefully; rerun with -Force if termination is intended.'
    } elseif ($wasForced) {
        'The process tree was force-stopped; the last in-flight turn may not have been persisted.'
    } else {
        'The Claw session was persisted and stopped.'
    }
} | ConvertTo-Json
