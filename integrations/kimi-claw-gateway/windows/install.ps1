$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = (Get-Command python -ErrorAction Stop).Source
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$StateDir = Join-Path $env:LOCALAPPDATA "KimiClawGateway"
$ApiKeyFile = Join-Path $StateDir "api-key.txt"

if (-not (Test-Path $VenvPython)) {
    & $Python -m venv (Join-Path $Root ".venv")
}

& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $Root "requirements.txt")
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

if (-not (Test-Path -LiteralPath $ApiKeyFile)) {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    [IO.File]::WriteAllText($ApiKeyFile, [Convert]::ToBase64String($bytes))
}

& (Join-Path $PSScriptRoot "set-private-acl.ps1") -StateDir $StateDir

Write-Host "Installed Kimi gateway runtime."
Write-Host "Protected session file: $(Join-Path $StateDir 'session.dpapi')"
Write-Host "Import from Edge: .\windows\import-edge-session.ps1"
