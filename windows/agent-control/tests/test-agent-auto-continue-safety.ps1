$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
. (Join-Path $repo 'windows\agent-control\common.ps1')

$unsafeSamples = @(
    'Request failed; the interactive session remains open: assistant stream produced no content',
    'No response, Please try again later.',
    'SyntaxError: Unexpected token ''<'', "<!doctype html>" is not valid JSON',
    'captcha_required: Aliyun CAPTCHA challenge detected',
    'upstream HTTP 403',
    'status code 429'
)
foreach ($sample in $unsafeSamples) {
    if ([string]::IsNullOrWhiteSpace((Get-ClawAutoContinueBlockReason -Text $sample))) {
        throw "Unsafe retry signal was not recognized: $sample"
    }
}
if ($null -ne (Get-ClawAutoContinueBlockReason -Text 'Do not bypass CAPTCHA/WAF; complete the safe offline work.')) {
    throw 'A benign safety instruction was incorrectly classified as an active CAPTCHA/WAF failure.'
}

$workspace = Join-Path $env:TEMP ('claw-agent-auto-continue-safety-' + [guid]::NewGuid().ToString('N'))
$log = Join-Path $workspace 'received.log'
$envLog = Join-Path $workspace 'environment.log'
$name = 'retry-safety'
$sessionId = 'session-retry-safety'
$sessionDir = Join-Path $workspace '.claw\sessions'

New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null
[System.IO.File]::WriteAllLines(
    (Join-Path $sessionDir "$sessionId.jsonl"),
    [string[]]@(
        '{"created_at_ms":1,"model":"fake-model","session_id":"' + $sessionId + '","type":"session_meta","updated_at_ms":1,"version":1}',
        '{"message":{"blocks":[{"text":"existing","type":"text"}],"role":"user"},"type":"message"}'
    ),
    [System.Text.UTF8Encoding]::new($false)
)

$env:FAKE_CLAW_LOG = $log
$env:FAKE_CLAW_ENV_LOG = $envLog
$env:FAKE_CLAW_REQUEST_FAILURE = '1'

try {
    & (Join-Path $repo 'windows\start-claw-agent.ps1') `
        -Name $name `
        -LauncherPath (Join-Path $PSScriptRoot 'fake-claw.cmd') `
        -WorkspacePath $workspace `
        -Resume latest `
        -AgentMode on `
        -IdlePromptRegex '^\s*>\s*$' `
        -AutoContinue `
        -AutoContinueDelaySeconds 1 `
        -AutoContinueMaxTurns 10 | Out-Null

    $deadline = (Get-Date).AddSeconds(15)
    $screenState = Join-Path $workspace ".claw\control\$name\screen.json"
    while (-not (Test-Path -LiteralPath $screenState) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 200
    }
    if (-not (Test-Path -LiteralPath $screenState)) { throw 'Controller screen state was not created.' }
    $status = $null
    do {
        Start-Sleep -Milliseconds 200
        try {
            $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
        }
        catch { $status = $null }
    } while (($null -eq $status -or $status.submitted_count -lt 2 -or $status.current_line -notmatch '^\s*>\s*$') -and (Get-Date) -lt $deadline)
    if ($null -eq $status) { throw 'Controller status was not readable.' }

    $sent = & (Join-Path $repo 'windows\send-claw-agent.ps1') `
        -Name $name `
        -WorkspacePath $workspace `
        -Message 'Perform exactly one safe action, then verify.' `
        -Mode next | ConvertFrom-Json

    $deadline = (Get-Date).AddSeconds(15)
    $task = $null
    do {
        Start-Sleep -Milliseconds 200
        try {
            $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
            $task = $status.auto_continue_task
        }
        catch { $task = $null }
    } while (($null -eq $task -or $task.state -ne 'blocked') -and (Get-Date) -lt $deadline)

    if ($null -eq $task -or $task.state -ne 'blocked') {
        throw 'A transport failure did not block auto-continue.'
    }
    if ([string]$task.block_reason -notmatch 'assistant stream produced no content') {
        throw "Unexpected block reason: $($task.block_reason)"
    }

    Start-Sleep -Seconds 2
    $lines = @(Get-Content -LiteralPath $log -Encoding utf8)
    $taskLines = @($lines | Where-Object { $_ -notlike '/resume *' -and $_ -notlike '/agent *' -and $_ -ne '/exit' })
    if ($taskLines.Count -ne 1) {
        throw "Unsafe automatic retry was submitted after a transport failure (task lines: $($taskLines.Count))."
    }
    [pscustomobject]@{
        passed = $true
        assertions = @(
            'CAPTCHA/WAF, HTML, HTTP 403/429, and empty responses are recognized',
            'benign CAPTCHA/WAF safety instructions do not trigger a false positive',
            'assistant stream produced no content blocks auto-continue',
            'blocked state records the detected reason',
            'no automatic retry is typed after the failure'
        )
    } | ConvertTo-Json -Depth 4
}
finally {
    try { & (Join-Path $repo 'windows\stop-claw-agent.ps1') -Name $name -WorkspacePath $workspace -TimeoutSeconds 2 -Force | Out-Null } catch {}
    Remove-Item Env:FAKE_CLAW_REQUEST_FAILURE -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $workspace -Recurse -Force -ErrorAction SilentlyContinue
}
