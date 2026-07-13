# CGM DealDesk

Automated daily triage of inbound single-family-home "deals" for CGM Ventures
(Dallas–Fort Worth). A local Python pipeline over the `deals@cgm-ventures.com`
Inbox. See [CONTEXT.md](CONTEXT.md), [ADR-0001](docs/adr/0001-local-python-pipeline.md),
[ADR-0002](docs/adr/0002-calculator-feed.md), and the phased plan in
[plans/deal-triage.md](plans/deal-triage.md).

## Status

**Phase 1 — Auth + read-only inbox discovery.** Ships the read half only:
service-account auth, the work-queue query, and a read-only Source-by-volume
discovery command. No writes, no labels, no AI.

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

One documented entry command:

```bash
dealdesk discover                 # read-only Source-by-volume tally
# or, without installing the console script:
python -m dealdesk discover
```

`discover` prints the resolved cutoff and query, then a Pareto ranking of
candidate Emails by Source over the activation window. It makes **no** write,
label, or AI call — it is safe to run repeatedly. Use `--config PATH` to point at
a different config, and `--today YYYY-MM-DD` to exercise the rolling cutoff.

## Tests

```bash
pytest
```
