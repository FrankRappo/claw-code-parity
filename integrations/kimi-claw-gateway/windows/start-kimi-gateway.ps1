param([int]$Port = 0)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$StateDir = Join-Path $env:LOCALAPPDATA "KimiClawGateway"
$PortFile = Join-Path $StateDir "port.txt"
if ($Port -le 0) {
    $ConfiguredPort = 0
    if ((Test-Path $PortFile) -and [int]::TryParse((Get-Content $PortFile -Raw).Trim(), [ref]$ConfiguredPort)) {
        $Port = $ConfiguredPort
    } else {
        $Port = 18081
    }
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Invalid Kimi gateway port: $Port"
}
$BaseUrl = "http://127.0.0.1:$Port"
$PersistentMessagesLimit = 1000
if ($env:KIMI_MAX_PERSISTENT_MESSAGES) {
    $PersistentMessagesLimit = 0
    if (-not [int]::TryParse($env:KIMI_MAX_PERSISTENT_MESSAGES, [ref]$PersistentMessagesLimit)) {
        throw "Invalid KIMI_MAX_PERSISTENT_MESSAGES: $($env:KIMI_MAX_PERSISTENT_MESSAGES)"
    }
}
$PersistentContextTokensLimit = 200000
if ($env:KIMI_MAX_PERSISTENT_CONTEXT_TOKENS) {
    $PersistentContextTokensLimit = 0
    if (-not [int]::TryParse($env:KIMI_MAX_PERSISTENT_CONTEXT_TOKENS, [ref]$PersistentContextTokensLimit)) {
        throw "Invalid KIMI_MAX_PERSISTENT_CONTEXT_TOKENS: $($env:KIMI_MAX_PERSISTENT_CONTEXT_TOKENS)"
    }
}
if ($PersistentMessagesLimit -lt 4) {
    throw "KIMI_MAX_PERSISTENT_MESSAGES must be at least 4."
}
if ($PersistentContextTokensLimit -lt 8000) {
    throw "KIMI_MAX_PERSISTENT_CONTEXT_TOKENS must be at least 8000."
}
$InstanceSuffix = if ($Port -eq 18081) { "" } else { ".$Port" }
$SessionFile = Join-Path $StateDir "session.dpapi"
$LegacySessionFile = Join-Path $StateDir "session.json"
$ApiKeyFile = Join-Path $StateDir "api-key.txt"
$PidFile = Join-Path $StateDir "gateway$InstanceSuffix.pid"
$StdoutLog = Join-Path $StateDir "gateway$InstanceSuffix.stdout.log"
$StderrLog = Join-Path $StateDir "gateway$InstanceSuffix.stderr.log"

if (-not (Test-Path $Python)) {
    throw "Run windows\install.ps1 first."
}
if (-not (Test-Path $SessionFile) -and (Test-Path $LegacySessionFile)) {
    & (Join-Path $PSScriptRoot "protect-session.ps1") `
        -InputPath $LegacySessionFile `
        -OutputPath $SessionFile `
        -RemovePlaintext
}
if (-not (Test-Path $SessionFile)) {
    throw "Kimi session is missing. Run windows\import-edge-session.ps1 first."
}
if (-not (Test-Path $ApiKeyFile)) {
    throw "Local gateway API key is missing. Run windows\install.ps1 first."
}
$GatewayApiKey = (Get-Content -LiteralPath $ApiKeyFile -Raw).Trim()

function Test-ExpectedGateway {
    param($Health)

    if (-not $Health) {
        return $false
    }
    $configurationMatches = `
        $Health.status -eq "ok" -and `
        $Health.upstream_model -eq "k2d6-chat" -and `
        $Health.session_protection -eq "dpapi" -and `
        $Health.persistent_chat -eq $true -and `
        [int]$Health.persistent_messages_limit -eq $PersistentMessagesLimit -and `
        [int]$Health.persistent_context_tokens_limit -eq $PersistentContextTokensLimit -and `
        $Health.local_api_auth -eq "required" -and `
        [int]$Health.max_concurrent_upstream -eq 1
    if (-not $configurationMatches) {
        return $false
    }
    try {
        $headers = @{ Authorization = "Bearer $GatewayApiKey" }
        $models = Invoke-RestMethod `
            -Uri "$BaseUrl/v1/models" `
            -Headers $headers `
            -TimeoutSec 2
        return $models.object -eq "list" -and @($models.data).Count -gt 0
    } catch {
        return $false
    }
}

$existingHealth = $null
try {
    $existingHealth = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 2
    if (Test-ExpectedGateway -Health $existingHealth) {
        Write-Host "Kimi gateway is already running."
        exit 0
    }
} catch {}

if ($existingHealth) {
    Write-Warning "A stale or incompatible Kimi gateway is running; restarting it."
    & (Join-Path $PSScriptRoot "stop-kimi-gateway.ps1") -Port $Port | Out-Host
    Start-Sleep -Milliseconds 500
    try {
        Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 2 | Out-Null
        throw "Port $Port is occupied by a service that the Kimi launcher cannot replace."
    } catch [System.Net.WebException] {}
}

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
$env:KIMI_SESSION_FILE = $SessionFile
$env:KIMI_GATEWAY_API_KEY = $GatewayApiKey
$env:KIMI_PROXY = ""
$env:KIMI_BIND_HOST = "127.0.0.1"
$env:KIMI_PORT = [string]$Port
$env:KIMI_UPSTREAM_PROTOCOL = "connect_v2"
$env:KIMI_UPSTREAM_MODEL = "k2d6-chat"
$env:KIMI_SCENARIO = "SCENARIO_CHAT"
$env:KIMI_KIMIPLUS_ID = ""
$env:KIMI_REASONING_EFFORT = "REASONING_EFFORT_NONE"
$env:KIMI_CONTEXT_LENGTH = "CONTEXT_LENGTH_L"
$env:KIMI_ENABLE_PLUGIN = "0"
$env:KIMI_DEFAULT_SHELL_TIMEOUT_MS = "30000"
$env:KIMI_MAX_CONCURRENT_UPSTREAM = "1"
$env:KIMI_REQUESTS_PER_MINUTE = "10"
$env:KIMI_FORMAT_REPAIR_ATTEMPTS = "5"
$env:KIMI_PERSISTENT_CHAT = "1"
$env:KIMI_MAX_PERSISTENT_MESSAGES = [string]$PersistentMessagesLimit
$env:KIMI_MAX_PERSISTENT_CONTEXT_TOKENS = [string]$PersistentContextTokensLimit
$GatewayScript = Join-Path $Root "src\kimi_api_server.py"

$StartOptions = @{
    FilePath = $Python
    ArgumentList = "`"$GatewayScript`""
    WorkingDirectory = $Root
    WindowStyle = "Hidden"
    RedirectStandardOutput = $StdoutLog
    RedirectStandardError = $StderrLog
    PassThru = $true
}
$Process = Start-Process @StartOptions

Set-Content -LiteralPath $PidFile -Value $Process.Id -Encoding ascii

for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 2
        if (Test-ExpectedGateway -Health $health) {
            Write-Host "Kimi gateway started. PID=$($Process.Id)"
            exit 0
        }
    } catch {}
}

throw "Kimi gateway did not become healthy. See $StderrLog"
