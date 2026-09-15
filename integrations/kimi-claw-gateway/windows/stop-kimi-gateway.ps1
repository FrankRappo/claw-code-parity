param([int]$Port = 0)
$ErrorActionPreference = "Stop"

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
$InstanceSuffix = if ($Port -eq 18081) { "" } else { ".$Port" }
$PidFile = Join-Path $StateDir "gateway$InstanceSuffix.pid"
$ProcessIds = [System.Collections.Generic.HashSet[int]]::new()

if (Test-Path $PidFile) {
    $RecordedPid = 0
    if ([int]::TryParse((Get-Content -LiteralPath $PidFile -Raw).Trim(), [ref]$RecordedPid)) {
        [void]$ProcessIds.Add($RecordedPid)
    }
}

$Processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like '*kimi-claw-gateway*src*kimi_api_server.py*' }
foreach ($Process in $Processes) {
    [void]$ProcessIds.Add([int]$Process.ProcessId)
}

try {
    $Health = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 2
    if ($Health.status -eq "ok" -and $Health.upstream_model) {
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { [void]$ProcessIds.Add([int]$_.OwningProcess) }
    }
} catch {}

foreach ($ProcessId in $ProcessIds) {
    Start-Process -FilePath taskkill.exe `
        -ArgumentList "/PID", $ProcessId, "/T", "/F" `
        -WindowStyle Hidden `
        -Wait `
        -ErrorAction SilentlyContinue | Out-Null
}

Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
Write-Host "Kimi gateway stopped. Port=$Port Processes=$($ProcessIds.Count)"
