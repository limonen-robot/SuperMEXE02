param(
    [ValidateSet('Build','Rebuild','Flash')]
    [string]$Action = 'Build'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$toolsRoot = Join-Path $env:LOCALAPPDATA 'Mbed Studio/mbed-studio-tools'
$python = Join-Path $toolsRoot 'python/python.exe'
$compiler = Join-Path $toolsRoot 'ac6/bin'
$license = Join-Path $env:LOCALAPPDATA 'Programs/Mbed Studio/tools-config/ac6-license.dat'
foreach ($required in @($python, $compiler, $license)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Mbed Studio tool not found: $required" }
}
$env:PATH = "$toolsRoot/python;$toolsRoot/python/Scripts;$compiler;$env:PATH"
$env:MBED_ARMC6_PATH = $compiler
$env:ARMLMD_LICENSE_FILE = $license
$env:ARMCOMPILER6_FROMELFOPT = '--ide=mbed'
Remove-Item Env:ARM_TOOL_VARIANT -ErrorAction SilentlyContinue
$buildPath = 'BUILD/VSCode/NUCLEO_G431KB/ARMC6/debug'
$buildArgs = @('-m','mbed','compile','-m','NUCLEO_G431KB','-t','ARMC6','--profile','debug','--build',$buildPath)
if ($Action -eq 'Rebuild') { $buildArgs += '-c' }
Push-Location -LiteralPath $projectRoot
try {
    & $python @buildArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($Action -eq 'Flash') {
        $boards = @(Get-PSDrive -Name D -PSProvider FileSystem -ErrorAction Stop)
        if ($boards.Count -eq 0) { throw 'NUCLEO-G431KB USB drive not found. Connect the ST-LINK USB port and retry.' }
        if ($boards.Count -ne 1) { throw 'Multiple NUCLEO-G431KB boards found. Connect only the intended board and retry.' }
        $binary = Join-Path $projectRoot "$buildPath/$(Split-Path $projectRoot -Leaf).bin"
        if (-not (Test-Path -LiteralPath $binary)) { throw "Firmware not found: $binary" }
        Copy-Item -LiteralPath $binary -Destination $boards[0].Root
        Start-Sleep -Seconds 3
        $failFile = Join-Path $boards[0].Root 'FAIL.TXT'
        if (Test-Path -LiteralPath $failFile) { throw (Get-Content -LiteralPath $failFile -Raw) }
        Write-Host "Firmware copied to $($boards[0].Root). Check the board LED/program."
    }
} finally { Pop-Location }
