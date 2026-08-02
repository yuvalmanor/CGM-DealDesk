<#
.SYNOPSIS
    Unattended daily DealDesk run. This is what Windows Task Scheduler invokes.

.DESCRIPTION
    A plain wrapper around `dealdesk run`: it calls the venv's python directly
    (no activation), tees the run's output into logs\dealdesk-<date>.log, and
    exits with the pipeline's exit code so Task Scheduler's "Last Run Result"
    means something.

    Credentials come from the environment (see README). They must be USER-scoped
    (setx) rather than set in a shell session, or the scheduled run won't see
    them. This script deliberately does not check for them: the pipeline already
    fails with a clear message naming the variable from the config, and
    duplicating those names here would be a second place to maintain them.

.PARAMETER DealdeskArgs
    Extra arguments forwarded verbatim to `dealdesk run`. The scheduled task
    passes none (a full live run). Handy for a manual smoke test, e.g.
        .\scripts\run-daily.ps1 --dry-run --no-ai --limit 1

.EXAMPLE
    .\scripts\run-daily.ps1
    A live run, exactly as the scheduled task fires it.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $DealdeskArgs
)

$ErrorActionPreference = 'Stop'

# The pipeline writes progress to stdout and warnings/errors to stderr. Merged
# with *>&1 under ErrorActionPreference='Stop', a single stderr line makes
# PowerShell raise a terminating NativeCommandError — aborting the wrapper BEFORE
# it can log the pipeline's output or the exit code, so an unattended failure
# leaves only the "starting" line and Task Scheduler's bare exit 1. Opt the native
# call out of that behavior so a nonzero exit or stderr is data, not a crash.
$PSNativeCommandUseErrorActionPreference = $false

# The pipeline's output is full of em-dashes and arrows. Piped to a file rather
# than a console, python falls back to the locale encoding and PowerShell decodes
# it as something else, landing U+FFFD in the log — corrupting the only record an
# unattended run leaves behind. Pin UTF-8 on both sides of the redirection.
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$repoRoot = Split-Path -Parent $PSScriptRoot
$python   = Join-Path $repoRoot '.venv\Scripts\python.exe'
$logDir   = Join-Path $repoRoot 'logs'
$logFile  = Join-Path $logDir ('dealdesk-{0:yyyy-MM-dd}.log' -f (Get-Date))

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log {
    param([string] $Message)
    $line = '[{0:yyyy-MM-dd HH:mm:ss}] {1}' -f (Get-Date), $Message
    Add-Content -Path $logFile -Value $line -Encoding utf8
    Write-Host $line
}

Write-Log '=== DealDesk daily run starting ==='

# Load the two credentials into THIS process from persisted (User, then Machine)
# scope, rather than trusting the ambient environment. A scheduled task launched
# under a logon token that predates a `setx`/User var sees a stale environment
# without it and fails to auth for no obvious reason — the exact failure that made
# the first 20:00 run exit 1 with an empty log. Reading the registry-backed value
# here makes the run self-sufficient. Never log the values, only present/absent.
foreach ($name in @('GOOGLE_SERVICE_ACCOUNT_KEY', 'ANTHROPIC_API_KEY')) {
    $val = [Environment]::GetEnvironmentVariable($name)  # process scope first
    if ([string]::IsNullOrEmpty($val)) {
        $val = [Environment]::GetEnvironmentVariable($name, 'User')
    }
    if ([string]::IsNullOrEmpty($val)) {
        $val = [Environment]::GetEnvironmentVariable($name, 'Machine')
    }
    if ([string]::IsNullOrEmpty($val)) {
        Write-Log "cred: $name MISSING (not in process, User, or Machine scope)"
    } else {
        Set-Item -Path "Env:$name" -Value $val
        Write-Log "cred: $name loaded (length $($val.Length))"
    }
}

if (-not (Test-Path $python)) {
    Write-Log "FATAL: no venv interpreter at $python"
    Write-Log 'Fix: python -m venv .venv; .venv\Scripts\python.exe -m pip install -e ".[dev]"'
    exit 2
}

# Run the pipeline, redirecting python's stdout and stderr STRAIGHT to files, then
# fold them into the log. Piping a native command's merged output through a
# PowerShell pipeline (Tee-Object) proved fragile under Task Scheduler: a failing
# run left only the "starting" line and a bare exit 1, the error nowhere. File
# redirection bypasses the pipeline entirely, so whatever python prints — including
# a traceback — is captured verbatim regardless of ErrorActionPreference. Continue
# keeps a stderr line or nonzero exit from aborting the wrapper before it logs.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$outFile = Join-Path $logDir 'run.stdout.tmp'
$errFile = Join-Path $logDir 'run.stderr.tmp'
try {
    & $python -m dealdesk run @DealdeskArgs 1>$outFile 2>$errFile
    $exitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
}
foreach ($capture in @($outFile, $errFile)) {
    if (Test-Path $capture) {
        Get-Content -Path $capture -Encoding utf8 | Add-Content -Path $logFile -Encoding utf8
        Remove-Item -Path $capture -Force -ErrorAction SilentlyContinue
    }
}

Write-Log "=== DealDesk daily run finished (exit $exitCode) ==="

# Keep the log directory from growing without bound on an unattended box.
Get-ChildItem -Path $logDir -Filter 'dealdesk-*.log' -File |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

exit $exitCode
