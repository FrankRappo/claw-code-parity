param(
    [Parameter(Mandatory = $true)]
    [string]$WorkspacePath
)

$resolved = [IO.Path]::GetFullPath($WorkspacePath).TrimEnd([char]92, [char]47)
$normalized = $resolved.ToLowerInvariant()
$sha256 = [Security.Cryptography.SHA256]::Create()

try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($normalized)
    $hex = -join ($sha256.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') })
    Write-Output ("workspace-{0}" -f $hex.Substring(0, 32))
}
finally {
    $sha256.Dispose()
}
