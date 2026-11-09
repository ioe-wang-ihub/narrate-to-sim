param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
try {
    if (-not $Python) {
        $Python = (& py -3.12 -c 'import sys; print(sys.executable)' | Select-Object -Last 1)
        if ($LASTEXITCODE -ne 0) { throw 'Install 64-bit Python 3.12, including pip and Tcl/Tk.' }
    }
    & $Python -I -c 'import sys,tkinter; assert sys.version_info[:2] == (3,12) and sys.maxsize > 2**32'
    if ($LASTEXITCODE -ne 0) { throw 'A full 64-bit Python 3.12 installation with Tcl/Tk is required.' }
    $VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        & $Python -I -m venv (Join-Path $ProjectRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Extract into a writable directory.' }
    }
    & $VenvPython -I -m pip --isolated install --require-virtualenv --index-url https://pypi.org/simple --only-binary=:all: -r (Join-Path $ProjectRoot 'requirements-stock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check Internet/proxy access to PyPI.' }
    & $VenvPython -I -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
    Write-Host 'Setup complete. Use the start entry described in this package README.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host $_ -ForegroundColor Red
    exit 1
}
