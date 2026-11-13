param([Parameter(Mandatory=$true)][string]$Python,[Parameter(Mandatory=$true)][string]$Compiler,[switch]$NoApp)
$ErrorActionPreference='Stop'
$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
& (Join-Path $PSScriptRoot 'stop.ps1')
& (Join-Path $PSScriptRoot 'build.ps1') -Compiler $Compiler
$serverScript=Join-Path $PSScriptRoot 'server.py'
$server=Start-Process -FilePath $Python -ArgumentList ('-X utf8 "'+$serverScript+'"') -WindowStyle Hidden -WorkingDirectory $PSScriptRoot -RedirectStandardOutput (Join-Path $PSScriptRoot 'build/server.log') -RedirectStandardError (Join-Path $PSScriptRoot 'build/server_error.log') -PassThru
$ids=@{server=$server.Id}
$ids | ConvertTo-Json | Set-Content (Join-Path $PSScriptRoot 'build/processes.json')
$ready=$false
for($attempt=0;$attempt -lt 100;$attempt++){
    if($server.HasExited){throw '仮想マイコンを起動できません。build/server_error.logを確認してください。'}
    $probe=New-Object Net.Sockets.TcpClient
    try{
        $task=$probe.ConnectAsync('127.0.0.1',48200)
        if($task.Wait(100) -and $probe.Connected){$ready=$true;break}
    }catch{}finally{$probe.Dispose()}
    Start-Sleep -Milliseconds 100
}
if(!$ready){throw '仮想マイコンの起動待ちが時間切れです。build/server_error.logを確認してください。'}
if(!$NoApp){
    $appScript=Join-Path $PSScriptRoot 'launch_pc.py'
    $app=Start-Process -FilePath $Python -ArgumentList ('-X utf8 "'+$appScript+'" --example') -WindowStyle Hidden -WorkingDirectory $PSScriptRoot -RedirectStandardOutput (Join-Path $PSScriptRoot 'build/app.log') -RedirectStandardError (Join-Path $PSScriptRoot 'build/app_error.log') -PassThru
    $ids.app=$app.Id
}
$ids | ConvertTo-Json | Set-Content (Join-Path $PSScriptRoot 'build/processes.json')
Write-Host '仮想マイコンを起動しました。実USBには接続しません。'
