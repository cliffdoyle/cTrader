# Installs the ICT Scanner to start automatically at logon (and restart itself if it stops).
# Usage: powershell -ExecutionPolicy Bypass -File install-autostart.ps1 [-NoCTraderStartup]
param([switch]$NoCTraderStartup)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $env:LOCALAPPDATA 'ictscan\venv'

if (-not (Test-Path (Join-Path $venv 'Scripts\pythonw.exe'))) {
    python -m venv $venv
    & (Join-Path $venv 'Scripts\python.exe') -m pip install --quiet -r (Join-Path $root 'requirements.txt')
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute (Join-Path $venv 'Scripts\pythonw.exe') -Argument '-m ictscan' -WorkingDirectory $root
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
# Watchdog: every 10 minutes; ignored while the scanner is already running, restarts it if it died.
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'ICT Scanner' -Action $action -Trigger $atLogon, $watchdog -Settings $settings -Principal $principal `
    -Description 'Read-only ICT market-structure scanner + dashboard (http://127.0.0.1:8765)' -Force | Out-Null
Start-ScheduledTask -TaskName 'ICT Scanner'
Write-Host 'Scheduled task "ICT Scanner" registered and started.'

if (-not $NoCTraderStartup) {
    $lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Pepperstone cTrader.lnk'
    $startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
    if ((Test-Path $lnk) -and -not (Test-Path (Join-Path $startup 'Pepperstone cTrader.lnk'))) {
        Copy-Item $lnk $startup
        Write-Host 'cTrader added to Windows startup.'
    }
}
Write-Host 'Dashboard: http://127.0.0.1:8765'
