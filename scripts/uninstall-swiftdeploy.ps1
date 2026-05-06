$ErrorActionPreference = "Stop"

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PyLauncher) {
    $PyCommand = @("py", "-3")
} else {
    $PyCommand = @("python")
}

# Capture the Scripts dir BEFORE uninstalling, so we can clean PATH afterwards.
$UserScripts = & $PyCommand[0] $PyCommand[1..($PyCommand.Length - 1)] -c "import sysconfig; print(sysconfig.get_path('scripts'))"

& $PyCommand[0] $PyCommand[1..($PyCommand.Length - 1)] -m pip uninstall -y swiftdeploy-cli
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: pip uninstall failed (exit $LASTEXITCODE). Continuing with PATH cleanup." -ForegroundColor Yellow
}

$CurrentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$RemainingPath = (($CurrentUserPath -split ";") | Where-Object { $_ -and $_ -ne $UserScripts }) -join ";"

[Environment]::SetEnvironmentVariable("Path", $RemainingPath, "User")
$env:Path = (($env:Path -split ";") | Where-Object { $_ -and $_ -ne $UserScripts }) -join ";"

Write-Host "SwiftDeploy uninstalled."
Write-Host "Removed $UserScripts from your user PATH if it was present."
