$ErrorActionPreference = "Stop"

python -m pip uninstall -y swiftdeploy-cli

$PythonVersion = python -c "import sys; print(f'Python{sys.version_info.major}{sys.version_info.minor}')"
$UserScripts = Join-Path $env:APPDATA "Python\$PythonVersion\Scripts"
$CurrentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$RemainingPath = (($CurrentUserPath -split ";") | Where-Object { $_ -and $_ -ne $UserScripts }) -join ";"

[Environment]::SetEnvironmentVariable("Path", $RemainingPath, "User")
$env:Path = (($env:Path -split ";") | Where-Object { $_ -and $_ -ne $UserScripts }) -join ";"

Write-Host "SwiftDeploy uninstalled."
Write-Host "Removed $UserScripts from your user PATH if it was present."
