param(
    [string]$Name = 'kimi',
    [string]$WorkspacePath = (Get-Location).Path,
    [Parameter(Mandatory, Position = 0)][string]$Message,
    [ValidateSet('now', 'next')][string]$Mode = 'now',
    [switch]$Raw
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'agent-control\common.ps1')

$stateDir = Get-ClawControlStateDir -WorkspacePath $WorkspacePath -Name $Name
$config = Get-ClawControlConfig -StateDir $stateDir
$hostState = Read-ClawJson -Path (Join-Path $stateDir 'host.json')
$dispatcherState = Read-ClawJson -Path (Join-Path $stateDir 'dispatcher.json')
if (-not $hostState -or -not (Test-ClawProcess -ProcessId $hostState.pid)) { throw "Claw session '$Name' is not running." }
if (-not $dispatcherState -or -not (Test-ClawProcess -ProcessId $dispatcherState.pid)) { throw "Claw dispatcher '$Name' is not running." }

$kind = if ($Raw) { 'raw' } else { 'instruction' }
$request = New-ClawControlRequest -StateDir $stateDir -Kind $kind -Message $Message -Mode $Mode
[pscustomobject]@{
    accepted = $true
    id = $request.id
    kind = $request.kind
    mode = $request.mode
    queue_path = (Join-Path $stateDir "queue\$($request.id).json")
    delivery_note = if ($Mode -eq 'now') { 'The current turn will be interrupted once if necessary.' } else { 'The message will be typed when Claw returns to its prompt.' }
} | ConvertTo-Json -Depth 4
