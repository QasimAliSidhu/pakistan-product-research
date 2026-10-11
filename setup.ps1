$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$runtimePath = Join-Path (Split-Path $projectRoot -Parent) 'lead-finder/.runtime/python/python.exe'
if (-not (Test-Path -LiteralPath $runtimePath)) { $runtimePath = (Get-Command python -ErrorAction Stop).Source }
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    & $runtimePath -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment setup failed.' }
}
$dependencyFile = if (Test-Path -LiteralPath 'requirements-lock.txt') { 'requirements-lock.txt' } else { 'requirements.txt' }
& '.venv/Scripts/python.exe' -m pip install -r $dependencyFile
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Ready. Run powershell -ExecutionPolicy Bypass -File .\start.ps1'
