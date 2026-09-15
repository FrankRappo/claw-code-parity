[CmdletBinding()]
param(
    [string]$InputPath = (Join-Path $env:LOCALAPPDATA "KimiClawGateway\session.json"),
    [string]$OutputPath = (Join-Path $env:LOCALAPPDATA "KimiClawGateway\session.dpapi"),
    [switch]$RemovePlaintext
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security

if (-not (Test-Path -LiteralPath $InputPath)) {
    throw "Plaintext Kimi session is missing: $InputPath"
}

$json = Get-Content -LiteralPath $InputPath -Raw
$session = $json | ConvertFrom-Json
foreach ($required in @("access_token", "refresh_token", "headers")) {
    if (-not $session.PSObject.Properties[$required] -or -not $session.$required) {
        throw "Kimi session is missing $required"
    }
}

$plainBytes = [Text.Encoding]::UTF8.GetBytes($json)
$protectedBytes = [Security.Cryptography.ProtectedData]::Protect(
    $plainBytes,
    $null,
    [Security.Cryptography.DataProtectionScope]::CurrentUser
)

$directory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$temporary = Join-Path $directory (".{0}.{1}.tmp" -f (Split-Path -Leaf $OutputPath), $PID)
try {
    [IO.File]::WriteAllBytes($temporary, $protectedBytes)
    Move-Item -LiteralPath $temporary -Destination $OutputPath -Force

    $roundTrip = [Security.Cryptography.ProtectedData]::Unprotect(
        [IO.File]::ReadAllBytes($OutputPath),
        $null,
        [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    $verified = [Text.Encoding]::UTF8.GetString($roundTrip) | ConvertFrom-Json
    if (-not $verified.access_token -or -not $verified.refresh_token) {
        throw "DPAPI session verification failed"
    }

    if ($RemovePlaintext) {
        Remove-Item -LiteralPath $InputPath -Force
    }
    & (Join-Path $PSScriptRoot "set-private-acl.ps1") -StateDir $directory
} finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
}

Write-Host "Kimi session protected with Windows DPAPI: $OutputPath"
