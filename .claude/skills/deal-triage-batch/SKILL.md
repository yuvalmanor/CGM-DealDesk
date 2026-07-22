---
name: deal-triage-batch
description: Run a live DealDesk triage batch that applies Gmail bucket labels (no sheet writes, no notifications) and produce a detailed report — category breakdown, AI-fallback usage and cost, and per-email Needs-Human reasoning. Use when the user wants to triage a batch of deal emails, label the inbox, or get an AI-usage/cost report for a DealDesk run (e.g. "run the triage tool", "triage N emails", "batch triage report").
---

# DealDesk batch triage + report

Wraps `dealdesk run --no-sheets --no-notify --limit N`: a **live** run that applies
Gmail bucket labels but writes **no** Triage Log row, feeds **no** Calculator deal,
and sends **no** notifications. Then it formats the run's structured JSON report.

⚠️ **Live and irreversible-ish:** labels are applied to the real
`deals@cgm-ventures.com` inbox. Because no Triage Log row is written, every labeled
email is marked "processed" with **no system-of-record row** — a later full
`dealdesk run` will *skip* it. Always confirm the count before running.

## Workflow

1. **Ask how many emails to run.** If the user already gave a number, use it. Never
   default to a number — get an explicit `N`.
2. **Confirm the plan and wait for a yes.** State plainly: "Live run — will apply
   labels to N emails in the real inbox, no sheet writes, no notifications. These N
   become labeled-but-unrecorded. Proceed?" Do not run until the user confirms.
3. **Run it** from the repo root (`C:\YuvalManorPrivate\CGM-DealDesk`). The env
   prelude loads the user-scoped API keys and forces UTF-8 (the console is cp1252 and
   crashes on emoji subjects otherwise):

   ```powershell
   $env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY','User'); $env:GOOGLE_SERVICE_ACCOUNT_KEY = [Environment]::GetEnvironmentVariable('GOOGLE_SERVICE_ACCOUNT_KEY','User'); $env:PYTHONIOENCODING = 'utf-8'; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $rp = "$env:TEMP\dealdesk-batch-report.json"; & .\.venv\Scripts\dealdesk.exe run --no-sheets --no-notify --limit <N> --report $rp
   ```

   Replace `<N>`. For a no-mutation preview, add `--dry-run` (reads + real AI calls,
   but applies no labels) — offer this if the user is unsure.
4. **Read the JSON report** at `$env:TEMP\dealdesk-batch-report.json` (path printed as
   `Report written:`). Every metric comes from this file — do not re-parse the
   console text. Schema and the cost formula are in [REFERENCE.md](REFERENCE.md).
5. **Produce the report** in the three sections below.
6. **Close with the consequence:** "N emails are now labeled-but-unrecorded; a full
   `dealdesk run` will skip them unless the labels are removed."

## Report format

Compute everything from the JSON (`emails_processed`, `counts`, `ai_used`,
`ai_by_bucket`, `fallback_reasons`, `ai_tokens`, `emails[]`).

**1. Category breakdown** — a table of each bucket in `counts` and its share of
`emails_processed`.

**2. AI usage**
   - **Fallback %** = `ai_used / emails_processed`.
   - **Total cost** = tokens × model rate (see [REFERENCE.md](REFERENCE.md); prefer
     the `claude-api` skill for current rates). Show input/output/total.
   - **Fallback % per bucket** = for each bucket in `ai_by_bucket`, `ai / total`.
   - **Most common fallback reason** = the first (highest-count) key of
     `fallback_reasons` — the must-have field the heuristics most often failed to
     recover — with a one-line plain-English gloss.

**3. Needs-Human — per email.** For every `emails[]` entry with
`bucket == "Needs-Human"`, list sender + subject and the per-property `reasons`
(e.g. `missing year_built`) that drove it there. If none, say so.
