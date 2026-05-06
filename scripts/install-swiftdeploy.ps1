$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

# Pick a Python interpreter. Prefer the `py` launcher because it ignores
# PATH ordering — a `.venv\Scripts` directory at the front of PATH would
# otherwise hijack `python` and (commonly) point us at a venv with no
# pip, silently no-op'ing the install.
$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PyLauncher) {
    $PyCommand = @("py", "-3")
} else {
    $PyCommand = @("python")
}

$PythonExe = & $PyCommand[0] $PyCommand[1..($PyCommand.Length - 1)] -c "import sys; print(sys.executable)"
Write-Host "Using Python: $PythonExe"

& $PyCommand[0] $PyCommand[1..($PyCommand.Length - 1)] -m pip --version > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: pip is not available in $PythonExe." -ForegroundColor Red
    Write-Host ""
    Write-Host "Bootstrap pip with:" -ForegroundColor Yellow
    Write-Host "    py -3 -m ensurepip --upgrade" -ForegroundColor Yellow
    exit 1
}

& $PyCommand[0] $PyCommand[1..($PyCommand.Length - 1)] -m pip install -e $ProjectRoot
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install failed (exit $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}

# Ask the interpreter directly where pip installs entry-point .exe files.
# The right answer differs by install flavor: a traditional per-user install
# uses %APPDATA%\Python\Python3xx\Scripts, but Microsoft Store / pythoncore
# Pythons put scripts alongside the interpreter under %LOCALAPPDATA%.
$UserScripts = & $PyCommand[0] $PyCommand[1..($PyCommand.Length - 1)] -c "import sysconfig; print(sysconfig.get_path('scripts'))"
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

Write-Host ""
Write-Host "SwiftDeploy installed. Test with: swiftdeploy --help" -ForegroundColor Green
