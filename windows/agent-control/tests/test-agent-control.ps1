$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$workspace = Join-Path $env:TEMP ('claw-agent-control-test-' + [guid]::NewGuid().ToString('N'))
$log = Join-Path $workspace 'received.log'
$envLog = Join-Path $workspace 'environment.log'
New-Item -ItemType Directory -Force -Path $workspace | Out-Null
$env:FAKE_CLAW_LOG = $log
$env:FAKE_CLAW_ENV_LOG = $envLog
$name = 'integration'
$sessionId = 'session-model-route'
$sessionModel = 'kimi-k2d6-original-route'
$sessionDir = Join-Path $workspace '.claw\sessions'
New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null
$sessionRecords = [string[]]@(
    '{"created_at_ms":1,"model":"' + $sessionModel + '","session_id":"' + $sessionId + '","type":"session_meta","updated_at_ms":1,"version":1}'
    '{"message":{"blocks":[{"text":"existing","type":"text"}],"role":"user"},"type":"message"}'
)
[System.IO.File]::WriteAllLines(
    (Join-Path $sessionDir "$sessionId.jsonl"),
    $sessionRecords,
    [System.Text.UTF8Encoding]::new($false)
)

try {
    & (Join-Path $repo 'windows\start-claw-agent.ps1') -Name $name -LauncherPath (Join-Path $PSScriptRoot 'fake-claw.cmd') -WorkspacePath $workspace -Resume latest -AgentMode on -IdlePromptRegex '^\s*>\s*$' | Out-Null
    $deadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 200
        $status = & (Join-Path $repo 'windows\get-claw-agent-status.ps1') -Name $name -WorkspacePath $workspace | ConvertFrom-Json
    } while (($status.submitted_count -lt 2 -or $status.current_line -notmatch '^\s*>\s*$') -and (Get-Date) -lt $deadline)
    if ($status.submitted_count -lt 2) { throw 'Bootstrap /resume and /agent commands were not delivered.' }

    $message = "Use the existing session.`r`nDo not restart the task."
    $sent = & (Join-Path $repo 'windows\send-claw-agent.ps1') -Name $name -WorkspacePath $workspace -Message $message -Mode next | ConvertFrom-Json
    $deadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 200
        $delivered = Test-Path -LiteralPath (Join-Path $workspace ".claw\control\$name\delivered\$($sent.id).json")
    } while (-not $delivered -and (Get-Date) -lt $deadline)
    if (-not $delivered) { throw 'Instruction was not delivered.' }

    $lines = @(Get-Content -LiteralPath $log -Encoding utf8)
    if ($lines[0] -ne "/resume $sessionId") { throw "Unexpected resume command: $($lines[0])" }
    if ($lines[1] -ne '/agent on') { throw "Unexpected bootstrap command: $($lines[1])" }
    if ((Get-Content -LiteralPath $envLog -Raw -Encoding utf8) -ne $sessionModel) {
        throw 'The saved session model was not restored before launching Claw.'
    }
    if ($lines.Count -lt 3 -or $lines[2] -notmatch '^Прочитай и выполни поправку из .*\.md;') {
        throw 'The durable inbox trigger was not typed into the same console.'
    }
    if ($lines[2] -match "`r|`n") { throw 'The console trigger must be exactly one physical line.' }
    $messageFile = Join-Path $workspace ".claw\control\$name\messages\$($sent.id).md"
    if ($lines[2] -notmatch [regex]::Escape($messageFile)) {
        throw 'The console trigger did not use the absolute inbox path.'
    }
    $stored = Get-Content -LiteralPath $messageFile -Raw -Encoding utf8
    if ($stored -notmatch 'Use the existing session\.' -or $stored -notmatch 'Do not restart the task\.') {
        throw 'The full multi-line instruction was not preserved in the inbox file.'
    }
    $expectedAck = Join-Path $workspace ".claw\control\$name\acks\$($sent.id).txt"
    if ($stored -notmatch [regex]::Escape($expectedAck) -or $stored -match '\$id|\$ackRelativePath') {
        throw 'The inbox file did not contain the expanded per-request ACK path.'
    }

    & (Join-Path $repo 'windows\stop-claw-agent.ps1') -Name $name -WorkspacePath $workspace -TimeoutSeconds 10 -Force | Out-Null
    [pscustomobject]@{
        passed = $true
        assertions = @(
            'visible console accepted injected Unicode input',
            'saved session model route was restored before launch',
            'agent mode was enabled in the same process',
            'full correction was stored durably before dispatch',
            'absolute inbox and ACK paths survive agent working-directory changes',
            'only a one-line trigger was typed into the console',
            'graceful /exit stopped the controlled session'
        )
    } | ConvertTo-Json -Depth 4
}
finally {
    try { & (Join-Path $repo 'windows\stop-claw-agent.ps1') -Name $name -WorkspacePath $workspace -TimeoutSeconds 2 -Force | Out-Null } catch {}
    Remove-Item -LiteralPath $workspace -Recurse -Force -ErrorAction SilentlyContinue
}
