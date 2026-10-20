# Install Pantheon Studio on Windows from the latest GitHub release.
#
#   irm https://raw.githubusercontent.com/adityaagarw/Pantheon/main/install.ps1 | iex
#
# $env:PANTHEON_VERSION = "2.0.1" picks a release instead of the latest.
# It installs for you only: no administrator rights needed.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is far slower with a progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$repo = "adityaagarw/Pantheon"
if ($env:PANTHEON_VERSION) {
    $api = "https://api.github.com/repos/$repo/releases/tags/v$($env:PANTHEON_VERSION.TrimStart('v'))"
} else {
    $api = "https://api.github.com/repos/$repo/releases/latest"
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Pantheon Studio needs 64-bit Windows."
}

try {
    $release = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "pantheon-install" }
} catch {
    throw "Couldn't read the release from GitHub ($api): $($_.Exception.Message)"
}
$asset = $release.assets | Where-Object { $_.name -like "*_x64-setup.exe" } | Select-Object -First 1
if (-not $asset) {
    throw "Release $($release.tag_name) has no Windows installer."
}

$setup = Join-Path ([IO.Path]::GetTempPath()) $asset.name
Write-Host "Downloading $($asset.name) ($([math]::Round($asset.size / 1MB)) MB)..."
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $setup -UseBasicParsing

Write-Host "Installing Pantheon Studio $($release.tag_name)..."
$p = Start-Process -FilePath $setup -ArgumentList "/S" -Wait -PassThru
Remove-Item $setup -ErrorAction SilentlyContinue
if ($p.ExitCode -ne 0) {
    throw "The installer stopped with exit code $($p.ExitCode)."
}

$exe = Join-Path $env:LOCALAPPDATA "Pantheon Studio\pantheon-studio.exe"
if (Test-Path $exe) {
    Write-Host "Installed. Starting Pantheon Studio..."
    Start-Process $exe
} else {
    Write-Host "Installed. Start Pantheon Studio from the Start menu."
}
