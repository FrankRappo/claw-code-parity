param(
    [Parameter(Mandatory)][string]$StateDir,
    [Parameter(Mandatory)][string]$LauncherPath,
    [Parameter(Mandatory)][string]$WorkspacePath,
    [AllowEmptyString()][string]$Resume = 'latest',
    [AllowEmptyString()][string]$Model = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$hostStatePath = Join-Path $StateDir 'host.json'
$startedAt = (Get-Date).ToUniversalTime().ToString('o')
$exitCode = -1

try {
    try { $Host.UI.RawUI.WindowTitle = "ClawCod controlled session - $(Split-Path $StateDir -Leaf)" } catch {}
    Write-ClawAtomicJson -Path $hostStatePath -Value ([ordered]@{
        pid = $PID
        state = 'running'
        started_at = $startedAt
        launcher_path = $LauncherPath
        workspace_path = $WorkspacePath
        resume = $Resume
        model = if ([string]::IsNullOrWhiteSpace($Model)) { $null } else { $Model }
    })

    Set-Location -LiteralPath $WorkspacePath
    $env:CLAW_AGENT_CONTROL_DIR = $StateDir
    if (-not [string]::IsNullOrWhiteSpace($Model)) {
        $env:KIMI_CLAW_MODEL = $Model
    }
    Write-Host '[Claw control] This console is durable and accepts live corrections.' -ForegroundColor Cyan
    Write-Host "[Claw control] State: $StateDir" -ForegroundColor DarkGray

    # `claw --resume latest` is a non-interactive inspection command and exits.
    # Start the REPL first; start-claw-agent.ps1 submits `/resume latest` into
    # this same live console before any user instruction.
    & $LauncherPath
    $exitCode = $LASTEXITCODE
}
catch {
    Write-Host "[Claw control] Host failure: $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}
finally {
    Write-ClawAtomicJson -Path $hostStatePath -Value ([ordered]@{
        pid = $PID
        state = 'stopped'
        started_at = $startedAt
        stopped_at = (Get-Date).ToUniversalTime().ToString('o')
        exit_code = $exitCode
        launcher_path = $LauncherPath
        workspace_path = $WorkspacePath
        resume = $Resume
        model = if ([string]::IsNullOrWhiteSpace($Model)) { $null } else { $Model }
    })
}

exit $exitCode
