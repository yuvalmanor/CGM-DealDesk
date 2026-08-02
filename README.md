# CGM DealDesk

Automated daily triage of inbound single-family-home "deals" for CGM Ventures
(Dallas–Fort Worth). A local Python pipeline over the `deals@cgm-ventures.com`
Inbox. See [CONTEXT.md](CONTEXT.md), [ADR-0001](docs/adr/0001-local-python-pipeline.md),
[ADR-0002](docs/adr/0002-calculator-feed.md), and the phased plan in
[plans/deal-triage.md](plans/deal-triage.md).

## Status

**Phase 7 — Scheduling.** Everything needed to run DealDesk hands-off now ships:
a Windows Task Scheduler definition that fires `dealdesk run` once a day as a
plain script (no agent loop, so idling costs nothing) and picks up a run missed
while the machine was off rather than skipping the day; a wrapper that logs each
run and surfaces its exit code; and a one-command installer.

**Not yet installed** — the task doesn't exist until you register it. See
[Scheduling](#scheduling--unattended-daily-run) to go live.

**Phase 6 — Re-send soft flag.** On ingest, each Property's address is reduced to
a normalized key and looked up against the Properties already in the Triage Log
(one scan per run, not per Email). A match stamps the new row's `resend_flag`
with a breadcrumb — `possible re-send — earlier row was Reject @ $400,000 on
2026-06-12` — which also surfaces in the Deal Notification, so a **price drop
reviving a deal you passed on** can't slip by.

It is deliberately a *soft* flag. Rows are **never merged**, prior rows are
**never re-evaluated**, and no Verdict changes: the breadcrumb only reports what
the earlier row said, and the operator decides. The normalizer is conservative on
purpose — it canonicalizes spelling (`St` ≡ `Street`, `Apt 4` ≡ `#4`, casing,
punctuation) and nothing else, because a *missed* re-send is a cheap miss while a
*false* one is a fabricated "same house" claim. An Email is never flagged as a
re-send of itself (the lookup excludes its own message-id), so a re-run is
idempotent.

**No address? The Email names the deal.** When extraction recovers no address,
the Triage row, the Deal Notification, the digest line and the fed Calculator row
all carry `<email subject>|<email sender>` in place of a blank — enough to find
the Email in Gmail and act on it. The label is display-only: the `|` marks it as
naming an Email rather than a house, and the normalizer refuses to key on it, so
two address-less blasts sharing a subject line are never called the same
property. `facts_json` still records only what the Source actually said.

Earlier phases are unchanged: the read half (Phase 1 — auth, work-queue query,
read-only `discover`); the triage spine (Phase 2 — extract → evaluate → roll up →
Triage Log → Bucket label **last**); the Calculator feed (Phase 3 — a *partial*
`Deal` into `DEALS_APP`, all five marker keys present, ARV `0` when unknown, real
`hmlLevPP`/`refiLtv`, idempotent by a deterministic row id); notifications
(Phase 4 — one Deal Notification per `Passed-BuyBox` Property behind a `notified`
guard, plus one Daily Digest per run); and `Error` retry with age-based
escalation to `Needs-Human` after `retry.escalate_after_days` (Phase 5 — Email
age stands in for a retry counter; there is no ledger).

## Setup

Requires Python ≥ 3.11 (for `tomllib`).

```bash
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# Git Bash / macOS / Linux:  source .venv/bin/activate
pip install -e ".[dev]"
```

### Credentials

DealDesk reuses the Calculator's Google **service account** (one credential for
Gmail + Sheets). Gmail access uses **domain-wide delegation**: the service
account impersonates the deals mailbox, so its client id must be granted the
`https://www.googleapis.com/auth/gmail.readonly` scope in the Google Workspace
admin console.

Supply the key JSON via the environment variable named in `config/dealdesk.toml`
(`auth.credential_env`, default `GOOGLE_SERVICE_ACCOUNT_KEY`) — the same variable
the Calculator uses:

```bash
export GOOGLE_SERVICE_ACCOUNT_KEY="$(cat service-account.json)"
```

## Configuration

All operator settings live in [`config/dealdesk.toml`](config/dealdesk.toml):
the inbox address, the **activation cutoff** (only Emails on/after it are
processed — move the date back to sweep older backlog), the Bucket labels that
mark an Email as already processed, and the auth wiring.

## Usage

```bash
dealdesk discover                       # read-only Source-by-volume tally (Phase 1)
dealdesk run                            # triage the work queue (Phase 2)
dealdesk run --dry-run                  # extract + evaluate + print; no writes/labels
dealdesk run --dry-run --limit 5 --verbose   # preview 5 Emails, per-Property detail
# or, without installing the console script:
python -m dealdesk run --dry-run
```

`discover` prints the resolved cutoff and query, then a Pareto ranking of
candidate Emails by Source over the activation window. It makes **no** write,
label, or AI call.

`run` triages each unprocessed Email: extract every Property, evaluate it against
the Buy Box, roll up to a Bucket, upsert the Triage Log, **feed every calc-ready
Property that *passed* the Buy Box into the Calculator's `DEALS_APP` tab**, and
apply the Bucket label last. The run summary ends with how many deals were fed.

Only a clean **Pass** feeds (narrowed 2026-08-02 — `Needs-Human` used to feed
too). Calc-ready and Pass answer different questions: calc-ready means the
Calculator's feed-required inputs are present, which says nothing about whether
any *gate* was cleared. `year_built` is a gate but is not feed-required, so a
Property whose Email never stated a year is calc-ready and Needs-Human at the
same time — and because a *missing* gate field evaluates to Needs-Human rather
than Reject, it reached `DEALS_APP` never having been measured against
`year_built >= 2000` at all. A Needs-Human Property still gets its Triage row,
its label, and its line in the Daily Digest; it just isn't entered as a Deal
until a human supplies the missing fact.

Two guards keep the pipeline from feeding on itself (both added 2026-08-01, after
the first live week showed each firing in production):

- **The work queue excludes DealDesk's own send addresses** (`inbox.address` and
  `notify.from`, see `Config.self_addresses`). Deal Notifications and the Daily
  Digest are sent *to* the mailbox DealDesk watches, so without this each run
  ingests the previous run's output as fresh deals — and because a Digest body
  lists every address in the run, the AI rung re-extracts all of them. That loop
  wrote **316 of the Triage Log's first 630 rows**. Excluded at the query, so a
  self-sent Email costs no read, no token, and can never reach a sheet.
- **The same-offer guard skips a duplicate Calculator write.** `feed_row_id` is
  keyed on (message-id, index), which makes a *re-run* idempotent but does
  nothing about the same house arriving in a *new* Email — one $289k deal
  occupied nine `DEALS_APP` rows. A Property whose **address and price both
  match** one already fed is not written again; its Triage row links to the
  existing `DEALS_APP` row instead. A re-send at a **different price still
  feeds** — a price drop reviving a dead deal is exactly what the re-send flag is
  for. The Triage row is always written (rows are never merged), and the run
  summary reports how many writes were skipped.

### Current operating mode (full pipeline, restored 2026-08-02)

**Everything is on.** Twice-daily unattended triage (07:00 + 20:00), AI fallback,
Triage Log writes, Gmail bucket labels, Deal Notifications + Daily Digest, and
Calculator insertion into `DEALS_APP`.

| Effect | Switch | State |
|---|---|---|
| Calculator insertion | `[calculator] enabled` | **on** — a calc-ready **Pass** is written to `DEALS_APP` and its Triage row carries the `deals_app_row_id` link |
| External notification CC | `[notify] deal_cc` | **on** — Deal Notifications are CC'd to `yuval.cgm@gmail.com`, the one recipient outside the deals mailbox (the Digest is not CC'd) |
| Unattended schedule | Task `CGM DealDesk Daily Triage` | **Ready** — two daily triggers |

Both switches were off from **2026-08-01** while the pipeline and Buy Box
guidelines were reworked; during that window every effect stayed inside the deals
mailbox and qualifying deals were entered into the Calculator by hand. They are
independent, and each is reversed by a deliberate edit to the config *and* to
`test_shipped_config_runs_the_full_pipeline` in `tests/test_config.py`, which
pins the shipped config's outward effects so neither drifts silently.

What changed in that window is why the feed is safe to run again: it is now
**Pass only**. A `Needs-Human` Property is no longer written, so the Calculator
can't fill with deals no gate ever cleared.

| Flag | Effect |
|---|---|
| `--dry-run` | Extract + evaluate + print only; writes nothing, applies no label. Still **reads** the Inbox and **still calls the AI fallback** (real tokens) when heuristics fall short. |
| `--limit N` | Process at most N Emails; pagination and per-Email fetches stop early, so it genuinely bounds Gmail reads and AI cost. Ideal for a first live test. |
| `--verbose` | Print each Email's Bucket, an `AI` marker when the fallback fired, and every Property's Verdict + calc-ready + reasons. |
| `--no-ai` | Disable the AI fallback (heuristics only) — **zero token cost**, for scouting a large batch. Emails heuristics can't fully parse fall back to their best-effort partial fields (usually `Needs-Human` on the missing gate), or `Not-A-Deal` if no facts were found. Accuracy is degraded vs. the full ladder; pair with `--dry-run` (a live `--no-ai` run can misfile Emails and drop them from the queue). |
| `--config PATH` | Point at a different config file. |
| `--today YYYY-MM-DD` | Override "today" for the rolling cutoff. |

The run summary always ends with `AI fallback used: X of N emails` so you can
watch fallback cost across runs.

Before the first live `run`, set `triage.spreadsheet_id` and
`calculator.spreadsheet_id` in the config (the latter is the Calculator's
`DEALS_APP` sheet — the same spreadsheet by default) and supply `ANTHROPIC_API_KEY`
for the AI fallback. `run` needs the `gmail.modify` and `spreadsheets` scopes
(discovery uses only `gmail.readonly`). The `[calculator]` section also carries
the standing financing assumptions (`hml_lev_pp`, `refi_ltv`) fed into every
Deal — keep them in sync with the Calculator's `DEFAULT_DEAL`.

## Scheduling — unattended twice-daily run

The scheduled run is a **Windows Task Scheduler** task invoking one PowerShell
script that exits when it's done — there's no resident agent, so firing costs
nothing while idle (ADR-0001). It fires **twice a day** (default 07:00 and
20:00) as two independent daily triggers.

```powershell
.\scripts\install-task.ps1                    # register, runs at 07:00 and 20:00
.\scripts\install-task.ps1 -At 06:30,19:30    # pick the two times (exactly two)
.\scripts\install-task.ps1 -PrintOnly         # review the task XML; register nothing
.\scripts\install-task.ps1 -Force             # replace an existing task
```

Replacing a **disabled** task additionally requires `-ReenableDisabled`: a plain
`-Force` unregisters and re-registers, which would come back *enabled* and
silently restart the automation you stopped.

No admin rights are needed: the task runs as you, at least privilege, with an
interactive token, so **no password is stored**.

### Credentials must be user-scoped

The scheduled run does not inherit variables you `export`ed in a shell. Set them
once, persistently, or the run fails with `Missing service-account key`:

```powershell
setx GOOGLE_SERVICE_ACCOUNT_KEY (Get-Content service-account.json -Raw)
setx ANTHROPIC_API_KEY "<key>"
```

### What it guarantees

| Setting | Why |
|---|---|
| `StartWhenAvailable` | A run missed while the machine was off, asleep, or logged out fires at the next opportunity — the day isn't skipped. |
| `DisallowStartIfOnBatteries: false` | Windows' default would skip that catch-up run on a laptop, defeating the above. |
| `WakeToRun: false` | A daily batch isn't worth waking the machine for; it self-heals on next boot (ADR-0001). |
| `MultipleInstancesPolicy: IgnoreNew` | A catch-up run landing on a scheduled one won't process the queue twice at once. |
| `LogonType: InteractiveToken` | Runs only while you're logged on, in exchange for storing no password. |

Because the task uses an interactive token, "next opportunity" means next logon,
not next boot. If the operator's machine is too often off, ADR-0001 names GitHub
Actions cron as the documented escape hatch.

### Checking on it

Each run appends to `logs\dealdesk-<date>.log` (pruned after 30 days); the Daily
Digest email remains the primary "the run happened" signal.

```powershell
Get-ScheduledTask -TaskName 'CGM DealDesk Daily Triage' | Get-ScheduledTaskInfo
Start-ScheduledTask -TaskName 'CGM DealDesk Daily Triage'    # fire it now
Get-Content .\logs\dealdesk-2026-07-17.log
.\scripts\run-daily.ps1 --dry-run --no-ai --limit 1          # smoke test, no writes, no tokens
```

`LastTaskResult` is the pipeline's own exit code: `0` success, `1` an operator-facing
error (missing credential, unset spreadsheet id), `2` a broken checkout (no venv).

### Buy Box configuration

The Buy Box field catalog lives under `[[buybox.fields]]` in
[`config/dealdesk.toml`](config/dealdesk.toml). Each field declares a **filter
role** (`gate`/`none`) and a **calc role** (`feed-required`/`feed-optional`/`none`);
a gate field also carries an `op` and `threshold`. The "must-have" fields
extraction must recover are *derived* from this one catalog (gate + feed-required
fields). The shipped values are **placeholders** (design examples) — tune every
threshold to the operator's real buying criteria before going live.

## Tests

```bash
pytest
```
