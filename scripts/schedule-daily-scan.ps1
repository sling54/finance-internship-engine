# Windows version of schedule-daily-scan.sh: runs the scan (then track and outreach send)
# every day at the time given, and the tracker sync every hour, with Task Scheduler.
#   .\scripts\schedule-daily-scan.ps1            # every day at 07:30
#   .\scripts\schedule-daily-scan.ps1 6 45       # every day at 06:45
#   .\scripts\schedule-daily-scan.ps1 off        # stop
param([string]$Hour = "7", [string]$Minute = "30")

$Repo = Split-Path -Parent $PSScriptRoot
$Exe = Join-Path $Repo ".venv\Scripts\jobengine.exe"
$Logs = Join-Path $Repo "logs"

Unregister-ScheduledTask -TaskName "jobengine-daily-scan" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "jobengine-track" -Confirm:$false -ErrorAction SilentlyContinue
if ($Hour -eq "off") {
    Write-Output "Daily scan and hourly tracker sync turned off."
    exit 0
}
if (-not (Test-Path $Exe)) {
    Write-Error "No $Exe yet. Create the virtual environment first (see README)."
    exit 1
}
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

$Daily = "& { `$log = Join-Path '$Logs' ('scan_' + (Get-Date -Format yyyy-MM-dd) + '.log'); " +
    "& '$Exe' scan *>> `$log; & '$Exe' track *>> `$log; & '$Exe' outreach send *>> `$log }"
$ScanAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command $Daily" -WorkingDirectory $Repo
$At = [datetime]::Today.AddHours([int]$Hour).AddMinutes([int]$Minute)
$ScanTrigger = New-ScheduledTaskTrigger -Daily -At $At
# StartWhenAvailable: if the laptop was asleep at that time, run as soon as it wakes.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "jobengine-daily-scan" -Action $ScanAction `
    -Trigger $ScanTrigger -Settings $Settings | Out-Null

$TrackAction = New-ScheduledTaskAction -Execute $Exe -Argument "track" -WorkingDirectory $Repo
$TrackTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "jobengine-track" -Action $TrackAction `
    -Trigger $TrackTrigger -Settings $Settings | Out-Null

Write-Output ("Daily scan at {0:D2}:{1:D2} and hourly tracker sync scheduled." -f [int]$Hour, [int]$Minute)
