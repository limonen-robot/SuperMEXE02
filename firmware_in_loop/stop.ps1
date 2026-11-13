$ErrorActionPreference='Stop'
$record=Join-Path $PSScriptRoot 'build/processes.json'
if(Test-Path -LiteralPath $record){
    $ids=Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
    foreach($property in $ids.PSObject.Properties){
        $processInfo=Get-CimInstance Win32_Process -Filter "ProcessId=$($property.Value)" -ErrorAction SilentlyContinue
        # PIDが再利用されていても、他のプロセスを止めない。
        if($processInfo -and $processInfo.CommandLine -and $processInfo.CommandLine.Contains($PSScriptRoot)){
            Stop-Process -Id $property.Value -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $record
}
