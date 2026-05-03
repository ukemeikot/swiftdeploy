$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

python -m pip install -e $ProjectRoot

$PythonVersion = python -c "import sys; print(f'Python{sys.version_info.major}{sys.version_info.minor}')"
$UserScripts = Join-Path $env:APPDATA "Python\$PythonVersion\Scripts"
$CurrentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")

if (($CurrentUserPath -split ";") -notcontains $UserScripts) {
    [Environment]::SetEnvironmentVariable("Path", "$CurrentUserPath;$UserScripts", "User")
    Write-Host "Added $UserScripts to your user PATH."
} else {
    Write-Host "$UserScripts is already on your user PATH."
}

if (($env:Path -split ";") -notcontains $UserScripts) {
    $env:Path = "$env:Path;$UserScripts"
    Write-Host "Updated PATH for this PowerShell session."
}

Write-Host "SwiftDeploy installed. Test with: swiftdeploy --help"
