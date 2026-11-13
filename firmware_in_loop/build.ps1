param([Parameter(Mandatory=$true)][string]$Compiler)
$ErrorActionPreference='Stop'
$simulationRoot=$PSScriptRoot
$sourceRoot=Split-Path $simulationRoot -Parent
$buildDir=Join-Path $simulationRoot 'build'
New-Item -ItemType Directory -Force $buildDir | Out-Null
$sourceFiles=@()
foreach($board in @('motor','sensor')) {
    $sourceFile=Join-Path $sourceRoot "G431KB_$board/main.cpp"
    if(!(Test-Path -LiteralPath $sourceFile)){throw "Missing firmware: $sourceFile"}
    $includePath=$sourceFile.Replace('\','/')
    $wrapper=Join-Path $buildDir "$board`_wrapper.cpp"
    [IO.File]::WriteAllText($wrapper,"#define main firmware_main`n#include `"$includePath`"`n#undef main`n")
    $boardRoot=Split-Path $sourceFile -Parent
    $ownFiles=@(Get-ChildItem -LiteralPath $boardRoot -Recurse -File | Where-Object {
        $_.Extension -in @('.c','.cpp','.h','.hpp') -and $_.FullName -notmatch '[\\/](mbed-os|BUILD|cmake_build)[\\/]'
    })
    $objects=@();$index=0
    foreach($extra in $ownFiles | Where-Object {$_.Extension -in @('.c','.cpp') -and $_.FullName -ne $sourceFile}) {
        $object=Join-Path $buildDir "$board`_$index.o";$index++
        $standard=if($extra.Extension -eq '.c'){'-std=c11'}else{'-std=c++17'}
        $language=if($extra.Extension -eq '.c'){'c'}else{'c++'}
        & $Compiler '-x' $language $standard '-O2' "-I$simulationRoot/native" "-I$boardRoot" '-c' $extra.FullName '-o' $object
        if($LASTEXITCODE -ne 0){throw "Build failed: $($extra.FullName)"};$objects+=$object
    }
    & $Compiler '-std=c++17' '-O2' '-shared' '-static' '-Wl,--no-insert-timestamp' "-I$simulationRoot/native" "-I$boardRoot" $wrapper "$simulationRoot/native/hal.cpp" @objects '-o' "$buildDir/$board.dll"
    if($LASTEXITCODE -ne 0){throw "Build failed: $board"}
    foreach($own in $ownFiles){$sourceFiles+=$own.FullName.Substring($sourceRoot.Length+1).Replace('\','/')}
}
$hashes=@{}
foreach($file in $sourceFiles){$hashes[$file]=(Get-FileHash (Join-Path $sourceRoot $file) -Algorithm SHA256).Hash}
$hashes | ConvertTo-Json | Set-Content (Join-Path $buildDir 'sources.json') -Encoding utf8
Write-Host '実機ソースを変更せず、motor.dll / sensor.dll を生成しました。'
