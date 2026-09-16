param([Parameter(Mandatory)][string]$LogPath)

$Host.UI.RawUI.WindowTitle = 'Fake Claw agent-control integration test'
if ($env:FAKE_CLAW_ENV_LOG) {
    [System.IO.File]::WriteAllText(
        $env:FAKE_CLAW_ENV_LOG,
        [string]$env:KIMI_CLAW_MODEL,
        [System.Text.UTF8Encoding]::new($false)
    )
}
while ($true) {
    Write-Host -NoNewline '> '
    $line = [Console]::ReadLine()
    if ($null -eq $line) { break }
    Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
    if ($line -eq '/exit') { break }
    if ($line -like '/agent *') { Write-Host "Agent mode: $($line.Substring(7))" }
    elseif ($env:FAKE_CLAW_REQUEST_FAILURE -eq '1' -and $line -notlike '/resume *') {
        Write-Host 'Request failed; the interactive session remains open: assistant stream produced no content'
    }
    else { Write-Host "FAKE RECEIVED: $line" }
}
