param([int]$Port = 2000, [string]$Python = '')
$ErrorActionPreference = 'Stop'
try {
    if (-not $Python) { $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
    if (-not (Test-Path -LiteralPath $Python)) { throw 'Run setup_narrate_to_sim.bat first.' }
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:CARLA_CACHE_DIR = Join-Path $PSScriptRoot 'runtime\carla-cache'
    New-Item -ItemType Directory -Force -Path $env:CARLA_CACHE_DIR | Out-Null
    & $Python -I -B -u (Join-Path $PSScriptRoot 'tools\stock_add_scenario.py') --port $Port --example
    exit $LASTEXITCODE
} catch {
    Write-Host $_ -ForegroundColor Red
    exit 1
}
