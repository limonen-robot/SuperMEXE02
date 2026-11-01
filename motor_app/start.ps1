$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $env:LOCALAPPDATA 'Mbed Studio/mbed-studio-tools/python/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Mbed Studio bundled Python was not found.'
}
& $pythonPath (Join-Path $PSScriptRoot 'app.py')
exit $LASTEXITCODE
