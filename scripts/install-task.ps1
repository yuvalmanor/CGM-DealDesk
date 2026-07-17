<#
.SYNOPSIS
    Register (or replace) the DealDesk daily triage task in Windows Task Scheduler.

.DESCRIPTION
    Fills the machine-specific placeholders in dealdesk-daily.xml — the start
    time, the current user, and this checkout's paths — and registers the result
    as a per-user task. No admin rights needed: the task runs as you, at
    LeastPrivilege, with an InteractiveToken, so no password is stored anywhere.

    Because the task uses an InteractiveToken it fires only while you're logged
    on; combined with StartWhenAvailable, a run missed while the machine was off,
    asleep, or logged out is picked up at the next opportunity rather than
    skipped for the day.

.PARAMETER TaskName
    Name to register under. Default 'CGM DealDesk Daily Triage'.

.PARAMETER At
    Local time of day to run, HH:mm. Default 07:00.

.PARAMETER Force
    Replace the task if it already exists.

.PARAMETER PrintOnly
    Print the resolved XML and register nothing. Use this to review exactly what
    would be installed.

.EXAMPLE
    .\scripts\install-task.ps1 -At 06:30
    Register the daily run for 06:30 local.

.EXAMPLE
    .\scripts\install-task.ps1 -PrintOnly
    Show the resolved task definition without touching Task Scheduler.
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'CGM DealDesk Daily Triage',
    [ValidatePattern('^\d{2}:\d{2}$')]
    [string] $At = '07:00',
    [switch] $Force,
    [switch] $PrintOnly
)

$ErrorActionPreference = 'Stop'

$repoRoot  = Split-Path -Parent $PSScriptRoot
$template  = Join-Path $PSScriptRoot 'dealdesk-daily.xml'
$runScript = Join-Path $PSScriptRoot 'run-daily.ps1'

foreach ($required in @($template, $runScript)) {
    if (-not (Test-Path $required)) { throw "Missing $required — is this checkout complete?" }
}

$time = [datetime]::ParseExact($At, 'HH:mm', [cultureinfo]::InvariantCulture)
# StartBoundary's date is just the schedule's anchor; DaysInterval=1 repeats it daily.
$startBoundary = (Get-Date -Hour $time.Hour -Minute $time.Minute -Second 0).ToString('yyyy-MM-ddTHH:mm:ss')

$xml = (Get-Content -Raw -Path $template).
    Replace('{{START_BOUNDARY}}', $startBoundary).
    Replace('{{USER_ID}}', "$env:USERDOMAIN\$env:USERNAME").
    Replace('{{SCRIPT_PATH}}', $runScript).
    Replace('{{REPO_ROOT}}', $repoRoot)

if ($xml -match '{{(\w+)}}') {
    throw "Unsubstituted placeholder {{$($Matches[1])}} left in the task XML — install-task.ps1 is out of sync with dealdesk-daily.xml."
}

if ($PrintOnly) {
    Write-Host "--- resolved task XML (nothing registered) ---`n"
    Write-Output $xml
    return
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    if (-not $Force) {
        throw "Task '$TaskName' already exists. Re-run with -Force to replace it."
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Replaced existing task '$TaskName'."
}

Register-ScheduledTask -TaskName $TaskName -Xml $xml | Out-Null

Write-Host "Registered '$TaskName' — runs daily at $At as $env:USERDOMAIN\$env:USERNAME."
Write-Host ''
Write-Host 'Verify:'
Write-Host "  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo   # next/last run time, last result"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'                         # fire it now"
Write-Host "  Get-Content .\logs\dealdesk-$(Get-Date -Format 'yyyy-MM-dd').log  # what it did"
Write-Host ''
Write-Host 'Credentials must be USER-scoped or the task will not see them:'
Write-Host '  setx GOOGLE_SERVICE_ACCOUNT_KEY "<the service-account JSON>"'
Write-Host '  setx ANTHROPIC_API_KEY "<key>"'
