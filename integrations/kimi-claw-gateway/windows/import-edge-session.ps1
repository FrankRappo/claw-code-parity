[CmdletBinding()]
param(
    [ValidateSet("Dedicated", "Existing")]
    [string]$ProfileMode = "Dedicated",
    [string]$ProfileDirectory = "Default",
    [ValidateRange(30, 900)]
    [int]$TimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security

$StateDir = Join-Path $env:LOCALAPPDATA "KimiClawGateway"
$OutputPath = Join-Path $StateDir "session.dpapi"
$EdgePath = Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path -LiteralPath $EdgePath)) {
    $EdgePath = Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"
}
if (-not (Test-Path -LiteralPath $EdgePath)) {
    throw "Microsoft Edge was not found."
}

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
& (Join-Path $PSScriptRoot "set-private-acl.ps1") -StateDir $StateDir

if ($ProfileMode -eq "Existing") {
    if (@(Get-Process msedge -ErrorAction SilentlyContinue).Count -gt 0) {
        throw "Close all Edge windows before importing the existing profile. This prevents profile corruption."
    }
    $UserDataDir = Join-Path $env:LOCALAPPDATA "Microsoft\Edge\User Data"
} else {
    $UserDataDir = Join-Path $StateDir "EdgeProfile"
    New-Item -ItemType Directory -Force -Path $UserDataDir | Out-Null
}

function Get-FreeTcpPort {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try {
        return ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    } finally {
        $listener.Stop()
    }
}

$script:CdpId = 0
$script:CapturedHeaders = @{}

function Receive-CdpMessage {
    param([Net.WebSockets.ClientWebSocket]$Socket)

    $stream = [IO.MemoryStream]::new()
    try {
        do {
            $buffer = New-Object byte[] 65536
            $segment = [ArraySegment[byte]]::new($buffer)
            $result = $Socket.ReceiveAsync($segment, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
            if ($result.MessageType -eq [Net.WebSockets.WebSocketMessageType]::Close) {
                throw "Edge closed the DevTools connection"
            }
            $stream.Write($buffer, 0, $result.Count)
        } while (-not $result.EndOfMessage)
        $json = [Text.Encoding]::UTF8.GetString($stream.ToArray())
        return $json | ConvertFrom-Json
    } finally {
        $stream.Dispose()
    }
}

function Capture-KimiHeaders {
    param($Message)

    if ($Message.method -ne "Network.requestWillBeSent") {
        return
    }
    $request = $Message.params.request
    if (-not $request -or ([string]$request.url -notmatch '^https://([^/]+\.)?(kimi\.com|kimi\.ai|moonshot\.cn)/')) {
        return
    }
    foreach ($property in @($request.headers.PSObject.Properties)) {
        $name = ([string]$property.Name).ToLowerInvariant()
        if ($name -in @(
            "authorization",
            "r-timezone",
            "user-agent",
            "x-language",
            "x-msh-device-id",
            "x-msh-platform",
            "x-msh-session-id",
            "x-msh-version",
            "x-traffic-id"
        )) {
            $script:CapturedHeaders[$name] = [string]$property.Value
        }
    }
}

function Invoke-CdpCommand {
    param(
        [Net.WebSockets.ClientWebSocket]$Socket,
        [string]$Method,
        [hashtable]$Parameters = @{}
    )

    $script:CdpId++
    $id = $script:CdpId
    $payload = @{ id = $id; method = $Method; params = $Parameters } | ConvertTo-Json -Depth 20 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($payload)
    $segment = [ArraySegment[byte]]::new($bytes)
    $Socket.SendAsync(
        $segment,
        [Net.WebSockets.WebSocketMessageType]::Text,
        $true,
        [Threading.CancellationToken]::None
    ).GetAwaiter().GetResult() | Out-Null

    while ($true) {
        $message = Receive-CdpMessage -Socket $Socket
        Capture-KimiHeaders -Message $message
        if ($message.id -eq $id) {
            if ($message.error) {
                throw "Edge DevTools command $Method failed: $($message.error.message)"
            }
            return $message.result
        }
    }
}

function Find-NamedString {
    param(
        $Node,
        [string[]]$Names,
        [int]$Depth = 0
    )

    if ($null -eq $Node -or $Depth -gt 12) {
        return $null
    }
    if ($Node -is [string]) {
        $trimmed = $Node.Trim()
        if (($trimmed.StartsWith("{") -and $trimmed.EndsWith("}")) -or
            ($trimmed.StartsWith("[") -and $trimmed.EndsWith("]"))) {
            try {
                return Find-NamedString -Node ($trimmed | ConvertFrom-Json) -Names $Names -Depth ($Depth + 1)
            } catch {}
        }
        return $null
    }
    if ($Node -is [Collections.IDictionary]) {
        foreach ($key in @($Node.Keys)) {
            $normalized = ([string]$key -replace '[^A-Za-z0-9]', '').ToLowerInvariant()
            if ($normalized -in $Names -and $Node[$key] -is [string] -and $Node[$key]) {
                return [string]$Node[$key]
            }
        }
        foreach ($key in @($Node.Keys)) {
            $found = Find-NamedString -Node $Node[$key] -Names $Names -Depth ($Depth + 1)
            if ($found) { return $found }
        }
        return $null
    }
    if ($Node -is [Collections.IEnumerable] -and $Node -isnot [string]) {
        foreach ($item in $Node) {
            $found = Find-NamedString -Node $item -Names $Names -Depth ($Depth + 1)
            if ($found) { return $found }
        }
        return $null
    }
    foreach ($property in @($Node.PSObject.Properties)) {
        $normalized = ([string]$property.Name -replace '[^A-Za-z0-9]', '').ToLowerInvariant()
        if ($normalized -in $Names -and $property.Value -is [string] -and $property.Value) {
            return [string]$property.Value
        }
    }
    foreach ($property in @($Node.PSObject.Properties)) {
        $found = Find-NamedString -Node $property.Value -Names $Names -Depth ($Depth + 1)
        if ($found) { return $found }
    }
    return $null
}

$Port = Get-FreeTcpPort
$arguments = @(
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=$Port",
    "--remote-allow-origins=http://127.0.0.1:$Port",
    "--user-data-dir=`"$UserDataDir`"",
    "--profile-directory=`"$ProfileDirectory`"",
    "--no-first-run",
    "--new-window",
    "https://www.kimi.com/"
)
$EdgeProcess = Start-Process -FilePath $EdgePath -ArgumentList $arguments -PassThru
$Socket = $null
try {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $target = $null
    while ([DateTime]::UtcNow -lt $deadline -and -not $target) {
        Start-Sleep -Milliseconds 500
        try {
            $targets = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/list" -TimeoutSec 2
            $target = @($targets | Where-Object { $_.type -eq "page" -and $_.url -match 'kimi\.(com|ai)' })[0]
        } catch {}
    }
    if (-not $target) {
        throw "Timed out waiting for the Kimi tab in Edge"
    }

    $Socket = [Net.WebSockets.ClientWebSocket]::new()
    $Socket.ConnectAsync(
        [Uri]$target.webSocketDebuggerUrl,
        [Threading.CancellationToken]::None
    ).GetAwaiter().GetResult()
    Invoke-CdpCommand -Socket $Socket -Method "Network.enable" | Out-Null
    Invoke-CdpCommand -Socket $Socket -Method "Page.enable" | Out-Null

    $accessToken = $null
    $refreshToken = $null
    $storage = $null
    $cookies = @()
    while ([DateTime]::UtcNow -lt $deadline -and (-not $accessToken -or -not $refreshToken)) {
        $expression = @'
(() => JSON.stringify({
  localStorage: Object.fromEntries(Object.entries(localStorage)),
  sessionStorage: Object.fromEntries(Object.entries(sessionStorage)),
  userAgent: navigator.userAgent,
  language: navigator.language,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
}))()
'@
        $evaluated = Invoke-CdpCommand -Socket $Socket -Method "Runtime.evaluate" -Parameters @{
            expression = $expression
            returnByValue = $true
            awaitPromise = $true
        }
        if ($evaluated.result.value) {
            $storage = ([string]$evaluated.result.value) | ConvertFrom-Json
            $accessToken = Find-NamedString -Node $storage -Names @("accesstoken", "authtoken")
            $refreshToken = Find-NamedString -Node $storage -Names @("refreshtoken")
        }

        $cookieResult = Invoke-CdpCommand -Socket $Socket -Method "Network.getAllCookies"
        $cookies = @($cookieResult.cookies | Where-Object {
            ([string]$_.domain).ToLowerInvariant() -match '(kimi\.com|kimi\.ai|moonshot\.cn)$'
        } | ForEach-Object {
            [ordered]@{
                name = [string]$_.name
                value = [string]$_.value
                domain = [string]$_.domain
                path = [string]$_.path
                expires = $_.expires
                secure = [bool]$_.secure
                httpOnly = [bool]$_.httpOnly
                sameSite = [string]$_.sameSite
            }
        })

        if (-not $accessToken -and $script:CapturedHeaders["authorization"] -match '^Bearer\s+(.+)$') {
            $accessToken = $Matches[1]
        }
        if (-not $accessToken -or -not $refreshToken) {
            Write-Host "Waiting for an authenticated Kimi session in Edge..."
            Invoke-CdpCommand -Socket $Socket -Method "Page.reload" -Parameters @{ ignoreCache = $false } | Out-Null
            Start-Sleep -Seconds 2
        }
    }

    if (-not $accessToken -or -not $refreshToken) {
        throw "Kimi access/refresh tokens were not found. Sign in to Kimi in the opened Edge profile and retry."
    }

    $headers = [ordered]@{
        authorization = "Bearer $accessToken"
        "connect-protocol-version" = "1"
        "content-type" = "application/json"
        "user-agent" = if ($script:CapturedHeaders["user-agent"]) { $script:CapturedHeaders["user-agent"] } else { [string]$storage.userAgent }
        "x-language" = if ($script:CapturedHeaders["x-language"]) { $script:CapturedHeaders["x-language"] } else { [string]$storage.language }
        "x-msh-platform" = "web"
    }
    foreach ($name in @("r-timezone", "x-msh-device-id", "x-msh-session-id", "x-msh-version", "x-traffic-id")) {
        $value = $script:CapturedHeaders[$name]
        if (-not $value) {
            $value = Find-NamedString -Node $storage -Names @(($name -replace '[^A-Za-z0-9]', '').ToLowerInvariant())
        }
        if ($value) {
            $headers[$name] = [string]$value
        }
    }

    if (-not $headers["x-msh-device-id"]) {
        throw "Kimi device metadata was not captured. Keep the Kimi page open until it finishes loading and retry."
    }

    $session = [ordered]@{
        capturedAt = [DateTime]::UtcNow.ToString("o")
        source = "Microsoft Edge ($ProfileMode/$ProfileDirectory)"
        access_token = $accessToken
        refresh_token = $refreshToken
        headers = $headers
        cookies = $cookies
    }
    $json = $session | ConvertTo-Json -Depth 20 -Compress
    $protectedBytes = [Security.Cryptography.ProtectedData]::Protect(
        [Text.Encoding]::UTF8.GetBytes($json),
        $null,
        [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    $temporary = Join-Path $StateDir ".session.dpapi.$PID.tmp"
    try {
        [IO.File]::WriteAllBytes($temporary, $protectedBytes)
        $roundTrip = [Security.Cryptography.ProtectedData]::Unprotect(
            [IO.File]::ReadAllBytes($temporary),
            $null,
            [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        $verified = [Text.Encoding]::UTF8.GetString($roundTrip) | ConvertFrom-Json
        if (-not $verified.access_token -or -not $verified.refresh_token) {
            throw "DPAPI session verification failed"
        }
        Move-Item -LiteralPath $temporary -Destination $OutputPath -Force
        & (Join-Path $PSScriptRoot "set-private-acl.ps1") -StateDir $StateDir
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }

    Write-Host "Imported the authenticated Kimi session from Edge."
    Write-Host "DPAPI file: $OutputPath"
    Write-Host "Kimi cookies captured: $($cookies.Count)"
} finally {
    if ($Socket) {
        $Socket.Dispose()
    }
    if ($EdgeProcess -and -not $EdgeProcess.HasExited) {
        Stop-Process -Id $EdgeProcess.Id -ErrorAction SilentlyContinue
    }
    foreach ($process in @(Get-CimInstance Win32_Process -Filter 'Name="msedge.exe"' -ErrorAction SilentlyContinue)) {
        if ([string]$process.CommandLine -match "--remote-debugging-port=$Port(?:\s|$)") {
            Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
        }
    }
}
