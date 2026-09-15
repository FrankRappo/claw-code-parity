Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-ClawControlStateDir {
    param(
        [Parameter(Mandatory)][string]$WorkspacePath,
        [Parameter(Mandatory)][string]$Name
    )

    if ($Name -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$') {
        throw 'Name must contain only letters, digits, dot, underscore, or hyphen (maximum 64 characters).'
    }

    $workspace = (Resolve-Path -LiteralPath $WorkspacePath).Path
    return Join-Path $workspace ".claw\control\$Name"
}

function Initialize-ClawControlState {
    param([Parameter(Mandatory)][string]$StateDir)

    foreach ($relative in @('queue', 'processing', 'delivered', 'failed', 'messages', 'acks')) {
        New-Item -ItemType Directory -Force -Path (Join-Path $StateDir $relative) | Out-Null
    }
}

function Write-ClawAtomicText {
    param(
        [Parameter(Mandatory)][string]$Path,
        [AllowEmptyString()][Parameter(Mandatory)][string]$Content
    )

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $temporary = Join-Path $directory ('.tmp-' + [guid]::NewGuid().ToString('N'))
    try {
        [System.IO.File]::WriteAllText($temporary, $Content, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Write-ClawAtomicJson {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]$Value
    )

    Write-ClawAtomicText -Path $Path -Content ($Value | ConvertTo-Json -Depth 12)
}

function Read-ClawJson {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json
}

function Test-ClawProcess {
    param([AllowNull()]$ProcessId)

    if (-not $ProcessId) { return $false }
    return $null -ne (Get-Process -Id ([int]$ProcessId) -ErrorAction SilentlyContinue)
}

function New-ClawControlRequest {
    param(
        [Parameter(Mandatory)][string]$StateDir,
        [Parameter(Mandatory)][ValidateSet('instruction', 'raw', 'interrupt')] [string]$Kind,
        [AllowEmptyString()][string]$Message = '',
        [Parameter(Mandatory)][ValidateSet('now', 'next')] [string]$Mode
    )

    Initialize-ClawControlState -StateDir $StateDir
    $id = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $messageRelativePath = $null
    $messagePath = $null

    if ($Kind -eq 'instruction') {
        if ([string]::IsNullOrWhiteSpace($Message)) { throw 'Instruction message cannot be empty.' }
        $messageRelativePath = ".claw\control\$((Split-Path $StateDir -Leaf))\messages\$id.md"
        $messagePath = [System.IO.Path]::GetFullPath((Join-Path $StateDir "messages\$id.md"))
        $ackPath = [System.IO.Path]::GetFullPath((Join-Path $StateDir "acks\$id.txt"))
        $body = @"
# ClawCod live correction

- Message ID: $id
- Received UTC: $((Get-Date).ToUniversalTime().ToString('o'))
- ACK path: $ackPath

Before continuing, use an available file-writing tool to create the ACK file above with the text ACK $id.

Persistent orchestration rules:
1. You have all ClawCod tools available; agent mode is ON and permissions were granted at launch. Use the tools needed to inspect, edit, run, and verify instead of claiming they are unavailable.
2. This is a correction to the CURRENT task in the CURRENT ClawCod session. Do not restart the task, open a new chat/session, or repeat completed investigation.
3. Treat newer control messages as authoritative for the affected branch of work, while preserving earlier non-conflicting requirements.
4. Finish the requested work and verify it with tests or concrete runtime evidence before reporting completion.

## Operator correction

$Message
"@
        Write-ClawAtomicText -Path $messagePath -Content $body
    }

    $request = [ordered]@{
        schema_version = 1
        id = $id
        kind = $Kind
        mode = $Mode
        message = if ($Kind -eq 'raw') { $Message } else { $null }
        message_relative_path = $messageRelativePath
        message_path = $messagePath
        created_at = (Get-Date).ToUniversalTime().ToString('o')
        status = 'queued'
        attempts = 0
    }
    $requestPath = Join-Path $StateDir "queue\$id.json"
    Write-ClawAtomicJson -Path $requestPath -Value $request
    return [pscustomobject]$request
}

function Get-ClawControlConfig {
    param([Parameter(Mandatory)][string]$StateDir)

    $config = Read-ClawJson -Path (Join-Path $StateDir 'config.json')
    if (-not $config) { throw "Claw agent control '$StateDir' has not been started." }
    return $config
}

function ConvertTo-ClawSingleLine {
    param([AllowEmptyString()][string]$Text)

    return ([regex]::Replace($Text, '\s+', ' ')).Trim()
}
