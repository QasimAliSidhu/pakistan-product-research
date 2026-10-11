param([int]$Port = 8790)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run setup.ps1 first.' }
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
    if ($health.app -eq 'pakistan-product-research') { Write-Host "Already running: http://127.0.0.1:$Port"; exit 0 }
} catch { }
Set-Location -LiteralPath $projectRoot
& $pythonPath -u app.py --port $Port
