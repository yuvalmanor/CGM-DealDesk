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

if (-not (Test-Path $python)) {
    Write-Log "FATAL: no venv interpreter at $python"
    Write-Log 'Fix: python -m venv .venv; .venv\Scripts\python.exe -m pip install -e ".[dev]"'
    exit 2
}

& $python -m dealdesk run @DealdeskArgs *>&1 | Tee-Object -FilePath $logFile -Append -Encoding utf8
$exitCode = $LASTEXITCODE

Write-Log "=== DealDesk daily run finished (exit $exitCode) ==="

# Keep the log directory from growing without bound on an unattended box.
Get-ChildItem -Path $logDir -Filter 'dealdesk-*.log' -File |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

exit $exitCode
