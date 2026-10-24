param([ValidateSet('install','status','run','disable','enable','remove')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$taskName = 'Top4Sec-SmartContract-RAG-DailyUpdate'
$taskProjectRoot = Split-Path -Parent $PSScriptRoot
$taskScript = Join-Path $PSScriptRoot 'run-paper-update.ps1'
$taskExisting = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($taskExisting -and -not ($taskExisting.Actions.Arguments -like ('*' + $taskScript + '*'))) {
    throw 'A task with this name belongs to a different location. No task was changed.'
}
if ($Action -eq 'install') {
    if ((Get-TimeZone).BaseUtcOffset.TotalHours -ne 8) { throw 'Daily 09:00 is configured for a UTC+8 Windows time zone. Adjust the trigger explicitly on this computer.' }
    if (-not (Test-Path -LiteralPath (Join-Path $taskProjectRoot '.venv\Scripts\python.exe'))) { throw 'Create this project virtual environment before installing its task.' }
    $taskPowerShell = (Get-Command powershell.exe).Source
    $taskCommand = New-ScheduledTaskAction -Execute $taskPowerShell -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $taskScript + '"') -WorkingDirectory $taskProjectRoot
    $taskTrigger = New-ScheduledTaskTrigger -Daily -At '09:00'
    $taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $taskIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskIdentity -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $taskCommand -Trigger $taskTrigger -Settings $taskSettings -Principal $taskPrincipal -Description 'Top4Sec paper catalog update at 09:00 Beijing time; full Docker project must be running; credentials read privately from project config.' -Force | Out-Null
    Write-Output 'Installed: daily 09:00 Beijing time. Runs as the current logged-in user; no password stored in task arguments.'
} elseif (-not $taskExisting) {
    if ($Action -eq 'status') { Write-Output 'Task not installed.'; exit 0 }
    throw 'Task not installed.'
} elseif ($Action -eq 'run') { Start-ScheduledTask -TaskName $taskName; Write-Output 'Started the existing scheduled task; inspect app.update_papers status for its result.' }
elseif ($Action -eq 'disable') { Disable-ScheduledTask -TaskName $taskName | Out-Null; Write-Output 'Disabled future triggers. An in-progress update is not cancelled.' }
elseif ($Action -eq 'enable') { Enable-ScheduledTask -TaskName $taskName | Out-Null; Write-Output 'Enabled future triggers.' }
elseif ($Action -eq 'remove') { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false; Write-Output 'Removed this project scheduled task. Existing data was retained.'; exit 0 }
$taskCurrent = Get-ScheduledTask -TaskName $taskName
$taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
[PSCustomObject]@{Name=$taskName;State=$taskCurrent.State;NextRunTime=$taskInfo.NextRunTime;LastRunTime=$taskInfo.LastRunTime;LastTaskResult=$taskInfo.LastTaskResult;DailyTime='09:00 Beijing (UTC+8)'} | Format-List
