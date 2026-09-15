[CmdletBinding()]
param(
    [string]$StateDir = (Join-Path $env:LOCALAPPDATA "KimiClawGateway")
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name

function Set-PrivatePathAcl {
    param([IO.FileSystemInfo]$Item)

    $permission = if ($Item.PSIsContainer) { "(OI)(CI)(F)" } else { "(F)" }
    & icacls.exe $Item.FullName /inheritance:r /grant:r "${identity}:$permission" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to set a private ACL on $($Item.FullName)"
    }
}

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
$items = @(Get-ChildItem -LiteralPath $StateDir -Force -Recurse -ErrorAction Stop)
foreach ($item in @($items | Sort-Object { $_.FullName.Length } -Descending)) {
    Set-PrivatePathAcl -Item $item
}
Set-PrivatePathAcl -Item (Get-Item -LiteralPath $StateDir -Force)
