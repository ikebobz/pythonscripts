# Creates (or replaces) a hidden background task that starts at logon, restarts
# itself if it stops, and has no time limit. Falls back to a Startup-folder
# shortcut if Windows does not allow the scheduled task to be created.
param(
    [Parameter(Mandatory = $true)] [string]$TaskName,
    [Parameter(Mandatory = $true)] [string]$PythonW,
    [Parameter(Mandatory = $true)] [string]$Script
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path $PythonW)) { Write-Host "[ERROR] Not found: $PythonW"; exit 1 }
$workDir = Split-Path -Parent $Script
$argLine = "`"$Script`""

# Stop an older copy that may be running from a previous setup.
Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$(Split-Path -Leaf $Script)*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

try {
    $action   = New-ScheduledTaskAction -Execute $PythonW -Argument $argLine -WorkingDirectory $workDir
    $trigger  = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
                  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                  -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Background task '$TaskName' created. It starts at logon and is running now."
}
catch {
    Write-Host "Could not create a scheduled task: $($_.Exception.Message)"
    Write-Host "Using a Startup-folder shortcut instead."
    $lnk = Join-Path ([Environment]::GetFolderPath('Startup')) "$TaskName.lnk"
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($lnk)
    $s.TargetPath = $PythonW
    $s.Arguments = $argLine
    $s.WorkingDirectory = $workDir
    $s.Save()
    Start-Process -FilePath $PythonW -ArgumentList $argLine -WorkingDirectory $workDir
    Write-Host "Shortcut created: $lnk  (starts at logon; running now)"
}
