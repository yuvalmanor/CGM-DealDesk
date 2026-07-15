# CGM DealDesk

Automated daily triage of inbound single-family-home "deals" for CGM Ventures
(Dallas–Fort Worth). A local Python pipeline over the `deals@cgm-ventures.com`
Inbox. See [CONTEXT.md](CONTEXT.md), [ADR-0001](docs/adr/0001-local-python-pipeline.md),
[ADR-0002](docs/adr/0002-calculator-feed.md), and the phased plan in
[plans/deal-triage.md](plans/deal-triage.md).

## Status

**Phase 5 — Error retry + age-based escalation.** `Error` is now a genuinely
*retryable* Bucket: the work-queue query no longer negates it, so a mid-run
failure is folded back in and retried on the next daily run. When a retry
succeeds (or an Email escalates), the stale `Error` label is stripped in the same
modify call so every Email still ends carrying **exactly one Bucket**. An `Error`
Email that keeps failing until it is older than `retry.escalate_after_days`
(default **3**) is relabeled `Needs-Human`, so a genuinely broken item reaches the
operator instead of retrying forever — **Email age stands in for a retry counter;
there is no ledger** (an unparseable `Date` header simply can't be aged and stays
`Error`).

Earlier phases are unchanged: the read half (Phase 1 — auth, work-queue query,
read-only `discover`); the triage spine (Phase 2 — extract → evaluate → roll up →
Triage Log → Bucket label **last**); the Calculator feed (Phase 3 — a *partial*
`Deal` into `DEALS_APP`, all five marker keys present, ARV `0` when unknown, real
`hmlLevPP`/`refiLtv`, idempotent by a deterministic row id); and notifications
(Phase 4 — one Deal Notification per `Passed-BuyBox` Property behind a `notified`
guard, plus one Daily Digest per run).

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
Pass/Needs-Human Property into the Calculator's `DEALS_APP` tab**, and apply the
Bucket label last. The run summary ends with how many deals were fed.

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
