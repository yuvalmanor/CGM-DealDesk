# 0001 — Local Python pipeline, OS-triggered daily, AI as last-resort extraction

**Status:** accepted

## Decision

DealDesk is a **local Python pipeline** triggered **once daily by Windows Task Scheduler**, talking directly to the Gmail API and Notion API. It is **not** a scheduled Claude agent, and it does **not** use the Gmail/Notion MCP connectors in production. Claude (the Anthropic API) is invoked **only as a last-resort extraction fallback**, and only for the specific decision-critical fields that cheaper deterministic methods failed to recover.

## Why

Cost is a first-class constraint: the tool must be as cheap as possible to run. Three consequences follow:

- **No idle-run token cost.** A scheduled Claude agent burns tokens on every wake, even when the inbox is empty. An OS-triggered script costs $0 to fire. Most Emails come from known Sources with consistent Templates, so a tiered extraction ladder (Template parser → generic heuristics → surgical LLM) keeps the bulk of extraction at $0 and calls the LLM only for the fields that defeat deterministic parsing.
- **Correctness guarantees want deterministic code.** The two headline invariants — *every Email ends in exactly one Bucket*, *nothing processed twice* — plus Buy Box gate evaluation are crisp logic that should be unit-testable and auditable, not re-reasoned by an LLM each run. Extraction (messy email/PDF → structured fields) is the only genuinely fuzzy step and is where the LLM earns its place.
- **Credential locality.** A single-operator tool holding a Gmail OAuth token, a Notion key, and an Anthropic key is safest keeping those on the operator's own machine rather than uploading them to a cloud secrets store.

## Considered and rejected

- **Scheduled Claude agent (MCP connector-native).** Fastest to stand up, but burns tokens on idle runs and puts correctness invariants inside a hard-to-test agent loop.
- **Google Apps Script time-trigger.** Free with native Gmail access, but runs JavaScript and cannot run Python PDF-parsing libraries (`pdfplumber`/`pypdf`), which the templated-PDF requirement makes mandatory.
- **Cloud cron (GCP Cloud Scheduler / GitHub Actions).** Viable and always-on, but adds cloud setup and requires trusting a cloud store with credentials. **GitHub Actions cron is the documented escape hatch** if the operator's machine is too often powered off — Task Scheduler's only real weakness (won't fire while the PC is asleep) is acceptable for a daily, non-urgent batch with "run as soon as possible after a missed start" enabled.

## Consequences

- The Gmail/Notion MCP connectors wired into the development environment are **not** the production integration path — a future reader should not "wire them in" expecting that to be the intended design.
- The pipeline only runs while the operator's machine is on; a missed daily run self-heals on next boot.
