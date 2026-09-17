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

    foreach ($relative in @('queue', 'processing', 'delivered', 'failed', 'messages', 'acks', 'completion')) {
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
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json
    }
    catch [System.Management.Automation.ItemNotFoundException] {
        # Atomic writers briefly replace the destination. Treat that race like
        # a missing snapshot; the next control-loop iteration will read it.
        return $null
    }
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
    $completionMarkerPath = $null
    $effectiveMessage = $Message

    if ($Kind -eq 'instruction') {
        if ([string]::IsNullOrWhiteSpace($Message)) { throw 'Instruction message cannot be empty.' }
        $controlConfig = Read-ClawJson -Path (Join-Path $StateDir 'config.json')
        if ($controlConfig -and $controlConfig.auto_continue -eq $true) {
            $completionMarkerPath = [System.IO.Path]::GetFullPath((Join-Path $StateDir "completion\$id.txt"))
            $effectiveMessage = @"
$Message

AUTO-CONTINUE COMPLETION CONTRACT: do not claim completion merely because one tool ran. Continue tool work across turns. Only after every requested change is implemented and verification has actually passed, use a file-writing tool to write exactly COMPLETE $id to $completionMarkerPath. If an external blocker makes further safe progress impossible, write BLOCKED $id followed by the precise reason instead. Do not create this marker early.
"@
        }
        $messageRelativePath = ".claw\control\$((Split-Path $StateDir -Leaf))\messages\$id.md"
        $messagePath = [System.IO.Path]::GetFullPath((Join-Path $StateDir "messages\$id.md"))
        $body = @"
# ClawCod live correction

- Message ID: $id
- Received UTC: $((Get-Date).ToUniversalTime().ToString('o'))

Persistent orchestration rules:
1. You have all ClawCod tools available; agent mode is ON and permissions were granted at launch. Use the tools needed to inspect, edit, run, and verify instead of claiming they are unavailable.
2. This is a correction to the CURRENT task in the CURRENT ClawCod session. Do not restart the task, open a new chat/session, or repeat completed investigation.
3. Treat newer control messages as authoritative for the affected branch of work, while preserving earlier non-conflicting requirements.
4. Finish the requested work and verify it with tests or concrete runtime evidence before reporting completion.
5. The controller records delivery automatically. Do not create ACK/receipt files and do not spend a tool call acknowledging this message; begin the operator correction itself immediately.

## Operator correction

$effectiveMessage
"@
        Write-ClawAtomicText -Path $messagePath -Content $body
    }

    $request = [ordered]@{
        schema_version = 1
        id = $id
        kind = $Kind
        mode = $Mode
        # Keep the instruction inline in the durable queue record as well as in
        # messages/. The dispatcher injects this text directly into the console;
        # making the model call read_file merely to discover its task lets that
        # administrative read satisfy `/agent on` without doing project work.
        message = if ($Kind -eq 'instruction') { $effectiveMessage } elseif ($Kind -eq 'raw') { $Message } else { $null }
        message_relative_path = $messageRelativePath
        message_path = $messagePath
        completion_marker_path = $completionMarkerPath
        delivery_receipt_path = if ($Kind -eq 'instruction') {
            [System.IO.Path]::GetFullPath((Join-Path $StateDir "acks\$id.txt"))
        } else { $null }
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

function Get-ClawAutoContinueBlockReason {
    param([AllowEmptyString()][string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }

    # Only inspect the recent console suffix. This keeps stale failures from an
    # earlier task from poisoning a later task while still covering Claw's
    # final provider/tool result immediately above the idle prompt.
    $lines = @($Text -split "`r?`n")
    $recent = ($lines | Select-Object -Last 24) -join "`n"
    $rules = @(
        [pscustomobject]@{
            Pattern = '(?i)assistant stream produced no content'
            Reason = 'assistant stream produced no content; possible CAPTCHA/WAF or transport block'
        },
        [pscustomobject]@{
            Pattern = '(?i)Request failed;\s*the interactive session remains open:'
            Reason = 'provider request failed; automatic retry suppressed'
        },
        [pscustomobject]@{
            Pattern = '(?i)No response,\s*Please try again later'
            Reason = 'upstream returned no response; automatic retry suppressed'
        },
        [pscustomobject]@{
            Pattern = '(?i)Unexpected token[^\r\n]*(?:!doctype|<html)[^\r\n]*not valid JSON'
            Reason = 'upstream returned HTML instead of JSON; possible CAPTCHA/WAF'
        },
        [pscustomobject]@{
            Pattern = '(?i)(?:captcha|hcaptcha|recaptcha|turnstile)[^\r\n]{0,80}(?:required|detected|blocked|challenge|appeared)'
            Reason = 'CAPTCHA challenge detected; automatic retry suppressed'
        },
        [pscustomobject]@{
            Pattern = '(?i)(?:waf)[^\r\n]{0,80}(?:blocked|challenge|detected|appeared)'
            Reason = 'WAF challenge detected; automatic retry suppressed'
        },
        [pscustomobject]@{
            Pattern = '(?i)(?:HTTP|status(?:\s+code)?)\s*[:=]?\s*(?:403|429)\b'
            Reason = 'upstream returned HTTP 403/429; automatic retry suppressed'
        }
    )

    foreach ($rule in $rules) {
        if ($recent -match $rule.Pattern) { return [string]$rule.Reason }
    }
    return $null
}
