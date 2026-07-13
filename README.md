# CGM DealDesk

Automated daily triage of inbound single-family-home "deals" for CGM Ventures
(Dallas–Fort Worth). A local Python pipeline over the `deals@cgm-ventures.com`
Inbox. See [CONTEXT.md](CONTEXT.md), [ADR-0001](docs/adr/0001-local-python-pipeline.md),
[ADR-0002](docs/adr/0002-calculator-feed.md), and the phased plan in
[plans/deal-triage.md](plans/deal-triage.md).

## Status

**Phase 2 — Tracer bullet: Email → Verdict → Bucket → Triage Log.** The full
triage spine end-to-end with **no Source Templates** — the Extraction Ladder runs
on generic heuristics + AI fallback only, over email bodies and attached PDFs.
For each Email: extract every Property, evaluate each against the Buy Box, roll
the Verdicts up to one Bucket, upsert a Triage Log row per Property (idempotent on
message-id + index), and apply the Bucket label **last**. A handled mid-run
failure lands the Email in `Error`. No Calculator feed, no notifications yet
(Phases 3–4).

Phase 1 (read half — service-account auth, the work-queue query, the read-only
`discover` command) is unchanged.

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
dealdesk discover                 # read-only Source-by-volume tally (Phase 1)
dealdesk run                      # triage the work queue (Phase 2)
dealdesk run --dry-run            # extract + evaluate + print; no writes/labels
# or, without installing the console script:
python -m dealdesk run --dry-run
```

`discover` prints the resolved cutoff and query, then a Pareto ranking of
candidate Emails by Source over the activation window. It makes **no** write,
label, or AI call.

`run` triages each unprocessed Email: extract every Property, evaluate it against
the Buy Box, roll up to a Bucket, upsert the Triage Log, and apply the Bucket
label last. `--dry-run` extracts and evaluates but writes nothing and applies no
label (it still reads the Inbox and may call the AI fallback). Both accept
`--config PATH` and `--today YYYY-MM-DD` (to exercise the rolling cutoff).

Before the first live `run`, set `triage.spreadsheet_id` in the config and
supply `ANTHROPIC_API_KEY` for the AI fallback. `run` needs the
`gmail.modify` and `spreadsheets` scopes (discovery uses only `gmail.readonly`).

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
