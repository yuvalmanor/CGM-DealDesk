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
    The two local times of day to run, each HH:mm. Default 07:00 and 20:00. The
    task template has exactly two daily triggers, so pass exactly two times.

.PARAMETER Force
    Replace the task if it already exists.

.PARAMETER PrintOnly
    Print the resolved XML and register nothing. Use this to review exactly what
    would be installed.

.PARAMETER ReenableDisabled
    Required to replace a task that is currently DISABLED. A task is disabled
    only because someone deliberately paused the unattended runs, but -Force
    unregisters it and registers a fresh ENABLED one — silently restarting them.
    This switch makes restarting a decision rather than a side effect.

.EXAMPLE
    .\scripts\install-task.ps1 -At 06:30,19:30
    Register the two daily runs for 06:30 and 19:30 local.

.EXAMPLE
    .\scripts\install-task.ps1 -PrintOnly
    Show the resolved task definition without touching Task Scheduler.
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'CGM DealDesk Daily Triage',
    [ValidateCount(2, 2)]
    [ValidatePattern('^\d{2}:\d{2}$')]
    [string[]] $At = @('07:00', '20:00'),
    [switch] $Force,
    [switch] $PrintOnly,
    [switch] $ReenableDisabled
)

$ErrorActionPreference = 'Stop'

$repoRoot  = Split-Path -Parent $PSScriptRoot
$template  = Join-Path $PSScriptRoot 'dealdesk-daily.xml'
$runScript = Join-Path $PSScriptRoot 'run-daily.ps1'

foreach ($required in @($template, $runScript)) {
    if (-not (Test-Path $required)) { throw "Missing $required — is this checkout complete?" }
}

# StartBoundary's date is just each trigger's anchor; DaysInterval=1 repeats it daily.
function Resolve-StartBoundary([string] $hhmm) {
    $t = [datetime]::ParseExact($hhmm, 'HH:mm', [cultureinfo]::InvariantCulture)
    return (Get-Date -Hour $t.Hour -Minute $t.Minute -Second 0).ToString('yyyy-MM-ddTHH:mm:ss')
}
$startBoundary1 = Resolve-StartBoundary $At[0]
$startBoundary2 = Resolve-StartBoundary $At[1]

$xml = (Get-Content -Raw -Path $template).
    Replace('{{START_BOUNDARY_1}}', $startBoundary1).
    Replace('{{START_BOUNDARY_2}}', $startBoundary2).
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
    # Replacing = unregister + register, and the fresh task comes back ENABLED.
    # If the operator had stopped the automation, that would restart it silently.
    if ($existing.State -eq 'Disabled' -and -not $ReenableDisabled) {
        throw "Task '$TaskName' exists but is DISABLED — someone paused the unattended runs. Replacing it would re-enable them. Re-run with -ReenableDisabled if that is what you want."
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Replaced existing task '$TaskName'."
}

Register-ScheduledTask -TaskName $TaskName -Xml $xml | Out-Null

# Register-ScheduledTask can emit a non-terminating error (e.g. a malformed XML
# declaration) that prints but doesn't stop the script — leaving a "Registered"
# message on a task that never actually got created. Verify before claiming success.
$registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $registered) {
    throw "Registration reported no terminating error but '$TaskName' does not exist — see the Task Scheduler error above."
}

Write-Host "Registered '$TaskName' — runs daily at $($At -join ' and ') as $env:USERDOMAIN\$env:USERNAME."
Write-Host ''
Write-Host 'Verify:'
Write-Host "  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo   # next/last run time, last result"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'                         # fire it now"
Write-Host "  Get-Content .\logs\dealdesk-$(Get-Date -Format 'yyyy-MM-dd').log  # what it did"
Write-Host ''
Write-Host 'Credentials must be USER-scoped or the task will not see them:'
Write-Host '  setx GOOGLE_SERVICE_ACCOUNT_KEY "<the service-account JSON>"'
Write-Host '  setx ANTHROPIC_API_KEY "<key>"'
