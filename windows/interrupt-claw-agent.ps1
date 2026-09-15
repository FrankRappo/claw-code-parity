param(
    [string]$Name = 'kimi',
    [string]$WorkspacePath = (Get-Location).Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'agent-control\common.ps1')
$stateDir = Get-ClawControlStateDir -WorkspacePath $WorkspacePath -Name $Name
$config = Get-ClawControlConfig -StateDir $stateDir
$request = New-ClawControlRequest -StateDir $stateDir -Kind interrupt -Mode now
[pscustomobject]@{ accepted = $true; id = $request.id; action = 'interrupt_current_turn' } | ConvertTo-Json
