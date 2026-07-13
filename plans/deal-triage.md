# Plan: DealDesk Triage Pipeline (`deal-triage`)

> Source PRD: [docs/prd/deal-triage.md](../docs/prd/deal-triage.md) — see also [CONTEXT.md](../CONTEXT.md), [ADR-0001](../docs/adr/0001-local-python-pipeline.md), [ADR-0002](../docs/adr/0002-calculator-feed.md)

Phased as tracer-bullet vertical slices. Phase 2 is a **template-free tracer** — the full spine end-to-end on generic + AI extraction only. Source Template parsers are added later, by volume (per the load-bearing phasing note recorded in project memory). Each phase is demoable/verifiable on its own.

## Architectural decisions

Durable decisions that apply across all phases (from the ADRs — do not re-litigate per phase):

- **Runtime**: local **Python** pipeline, one entry command, triggered **once daily by Windows Task Scheduler** (ADR-0001). Not a Claude agent; no MCP connectors in production. Anthropic API is a last-resort extraction fallback only.
- **Auth**: one **Google service account**, reused from the Calculator, covering Gmail + Sheets. Single credential on the operator's machine.
- **Stores** (all Google/Gmail, one auth):
  - **Bucket** = a **Gmail label** on the Email = email-level workflow state. Five terminal-or-retryable buckets: `Passed-BuyBox`, `Needs-Human`, `Rejected`, `Not-A-Deal`, `Error` (retryable).
  - **Triage Log** = Google Sheet tab **`DEALS_TRIAGE`** = per-Property system of record + idempotency backstop + re-send lookup source. Keyed on **Gmail message-id + property index**.
  - **Calculator** = Google Sheet tab **`DEALS_APP`** (owned by `brrrr-calculator`) = downstream write-only sink. Keyed on the stored **`DEALS_APP` row id**.
- **Calculator contract** (ADR-0002): write a **partial `Deal`** row. Marker-key guard requires all of `purchasePrice, arv, monthlyRent, hmlLevPP, refiLtv` present. `arv` written as **`0`** when unknown; assumption markers `hmlLevPP`/`refiLtv` carry **real values, never `0`**. Idempotent update by stored row id.
- **Key models / modules** (stable interfaces, pure where possible):
  - `Buy Box Evaluator`: `(property_fields, buybox_config) → Verdict + reasons + calc_ready + missing_fields`. Filter-role fields decide the Verdict (present-&-fails → Reject; missing → Needs-Human; else Pass; Reject short-circuits). Calc-ready = all feed-required calc inputs present.
  - `Bucket Rollup`: `[Verdict…] → Bucket`, precedence `Passed-BuyBox > Needs-Human > Rejected`; no Property → `Not-A-Deal`.
  - `Deal Input Builder`: `property_fields → partial Deal dict` (ADR-0002 contract).
  - `Address Normalizer`: `raw_address → normalized_key` (pure), used by re-send lookup.
  - `Extraction Ladder`: `Email → [property_fields]`, internally Template parsers → generic heuristics → AI fallback (fallback behind an interface so it can be faked).
  - `Notification Builder`: `(property, bucket, links) → email content`.
  - Thin I/O adapters: `Gmail Gateway`, `Sheets Gateway`.
- **Config**: the Buy Box field catalog (each field's filter role + calc role, each gate's threshold) lives in a **config file**, authored by the operator — never hard-coded. `year-built`, `rent`, `ARV` are design examples, not the final catalog.
- **Invariants preserved throughout**: (1) every Email ends in **exactly one Bucket**; (2) **nothing processed twice** (label-last + message-id upsert); (3) **no side effect duplicated on retry** (idempotent Triage/Calculator writes, `notified` guard on notifications).
- **Orchestrator ordering** (per Email): extract → evaluate each Property → rollup → **upsert Triage Log** → **feed Calculator** → **apply Bucket label last** → send Notifications / accumulate Digest.
- **Testing discipline**: golden/table tests through public interfaces for the pure modules; mocked-Google integration tests for gateways; one happy-path + one **crash-recovery** integration test for the Orchestrator. Mirror the sibling `brrrr-calculator` golden-file style in pytest.

---

## Phase 1: Auth + read-only inbox discovery

**User stories**: 2, 3, 4, 44 (+ the PRD "early discovery" step)

### What to build

Project scaffold (Python, pytest, config loader, single entry point) and the **read** half of the Gmail Gateway using the shared service-account auth. Build the work-queue query: Emails in the `deals@cgm-ventures.com` Inbox, **on/after the activation cutoff** (default two weeks back, configurable and deliberately movable), that are unlabeled. On top of it, ship a **read-only discovery command** that samples the activation window and **tallies candidate Emails by Source** (sender domain/subject), producing the Pareto ranking that orders the Phase 8 Template work. No writes, no labels, no AI.

### Acceptance criteria

- [x] `pip install` / venv + pytest run cleanly; one documented entry command exists.
- [x] Service-account auth loads the shared Calculator credential and authorizes Gmail read. _(Verified live: `cgm-deal-calc-sheets` SA + domain-wide delegation for gmail.readonly authorized a real read of `deals@cgm-ventures.com`.)_
- [x] Work-queue query returns only Inbox Emails on/after the cutoff; changing the cutoff config changes the set (and can be moved back to sweep older backlog). _(Query scoping + cutoff-sensitivity verified live and by unit/gateway tests.)_
- [x] Discovery command prints a Source-by-volume tally over the activation window. _(Verified live: 437 candidate Emails tallied into a Pareto ranking; ~7 Sources cover 80%.)_
- [x] No message is labeled, moved, or written anywhere during a discovery run. _(Gateway only calls list + get(metadata); read-only scope; gateway test asserts no mutating call; no write code exists in Phase 1.)_

---

## Phase 2: Tracer bullet — Email → Verdict → Bucket → Triage Log (label-last)

**User stories**: 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 17, 18, 25, 34, 35, 36 (email/message-id part), 38 (AI-only ladder), 39, 40, 41

### What to build

The end-to-end triage spine with **no Source Templates** — the Extraction Ladder runs on generic heuristics + AI fallback only, over both **email bodies and attached PDFs**. For each Email: extract every Property, evaluate each independently against the Buy Box config (filter-role fields decide Verdict; Reject short-circuits missing data; must-have fields derived from the active Buy Box; yield never a rejection reason), roll the Verdicts up to one Bucket, **upsert** a Triage Log row per Property (keyed on message-id + index), and **apply the Bucket label last**. A mid-run failure lands the Email in `Error`. Label-last + idempotent upsert make a crash before the label safely re-processable, and guarantee every Email ends in exactly one Bucket. No Calculator feed, no notifications yet.

### Acceptance criteria

- [ ] Each Property is evaluated independently; a multi-property Email's Bucket is the best Verdict (`Passed-BuyBox > Needs-Human > Rejected`); no Property → `Not-A-Deal`.
- [ ] Verdict matrix holds: present-&-fails-gate → Reject; missing filter field → Needs-Human; all gates pass → Pass; a confident Reject short-circuits missing data.
- [ ] Must-have fields are derived from the active Buy Box config (one config, not two); rent-to-price yield is never a rejection reason.
- [ ] Facts are extracted from both email bodies and attached PDFs; unknown Sources still parse via the AI fallback.
- [ ] Every Property is written as one Triage Log row with facts, Verdict, reasons, and Calc-ready flag; the upsert is idempotent on message-id + index.
- [ ] The Bucket label is applied **last**; an Email is "done" only once labeled; a crash after the Triage write but before the label re-runs without duplicating the row (crash-recovery integration test passes).
- [ ] Golden/table tests cover the Evaluator and Bucket Rollup decision matrices.

---

## Phase 3: Calculator feed

**User stories**: 19, 20, 21, 22, 23, 24, 26, 36 (Calculator part)

### What to build

Add the downstream Calculator sink. The Deal Input Builder maps DealDesk Property fields to a **partial `Deal`** honoring the ADR-0002 contract, and the Sheets Gateway writes it to `DEALS_APP`. Feed a Property **iff Calc-ready AND Verdict is Pass or Needs-Human** — Reject is never fed. Feed-optional inputs (ARV) missing → still fed, written `arv: 0`. Store the created `DEALS_APP` row id on the Triage Log row and **update** it on re-run rather than appending. Fed deals inherit standing financing assumptions from the Calculator's own `DEFAULT_DEAL`.

### Acceptance criteria

- [ ] A Calc-ready Pass or Needs-Human Property is written to `DEALS_APP`; a Reject is never written.
- [ ] A Property missing a feed-required input (e.g. rent) is not fed; one missing only a feed-optional input (ARV) is fed with `arv: 0`.
- [ ] Every written row carries all five marker keys; `hmlLevPP`/`refiLtv` carry real model values (never `0`).
- [ ] The Triage Log row stores and links to the `DEALS_APP` row id; a re-run updates that row rather than creating a duplicate (idempotent feed).
- [ ] Deal Input Builder golden tests cover marker-key presence, the `arv: 0` sentinel, real assumption markers, and the field mapping.

---

## Phase 4: Notifications — per-deal + Daily Digest

**User stories**: 29, 30, 31, 32, 33, 37

### What to build

The Notification Builder plus the Gmail Gateway **send** path (from `deals@cgm-ventures.com` so the operator's existing "Deal Notifications" filter routes them). Send one **Deal Notification** per `Passed-BuyBox` Property carrying address, Source, price/rent/ARV, Verdict + why, a link to the Calculator row, and any re-send flag — guarded by a **`notified`** flag on the Triage Log row so a retry never re-emails. Accumulate and send one **Daily Digest** per run: counts and one-line listings across all Buckets, with `Needs-Human` items listed alongside what's missing.

### Acceptance criteria

- [ ] Exactly one Deal Notification per `Passed-BuyBox` Property; a re-run does not re-send (`notified` guard verified).
- [ ] The notification carries address, Source, price/rent/ARV, Verdict + reasons, Calculator-row link, and re-send flag when present; it is sent from `deals@cgm-ventures.com`.
- [ ] Exactly one Daily Digest per run, with per-Bucket counts and one-line listings including `Rejected`/`Error`.
- [ ] `Needs-Human` Properties appear in the Digest with what's missing (no per-deal email for them).
- [ ] Notification Builder golden tests cover per-deal and digest content, including the Needs-Human summary.

---

## Phase 5: Error retry + age-based escalation

**User stories**: 10, 11

### What to build

Fold `Error`-labeled Emails back into the work queue so they retry on every daily run, and add age-based escalation: an `Error` Email older than ~3 days that keeps failing is relabeled `Needs-Human` (the Email's age stands in for a retry counter — no ledger).

### Acceptance criteria

- [ ] `Error` Emails within the age window are picked up and retried on the next run; a transient failure that later succeeds ends in its correct terminal Bucket.
- [ ] An `Error` Email older than ~3 days that still fails is escalated to `Needs-Human`.
- [ ] Escalation uses Email age only; no retry-count ledger is introduced.

---

## Phase 6: Re-send soft flag

**User stories**: 27, 28

### What to build

On ingest, run a cheap normalized-address lookup (Address Normalizer + a scan of prior Triage Log rows). If a prior Property matches, stamp the new row with a breadcrumb (e.g. "possible re-send — earlier row was `Rejected` @ $300k"). Rows are **never merged** and old rows are **never re-evaluated** — the flag exists only so a price-drop reviving a dead deal isn't missed.

### Acceptance criteria

- [ ] A new Property whose normalized address matches a prior Triage Log row is stamped with a re-send breadcrumb referencing the earlier row's Verdict/price.
- [ ] Re-sends are recorded as new rows; no row is ever merged and no prior row is re-evaluated.
- [ ] The breadcrumb surfaces in the Deal Notification (Phase 4) when present.
- [ ] Address Normalizer golden tests cover equivalence ("St" ≡ "Street", unit suffixes, casing/whitespace) and non-equivalence (distinct houses stay distinct).

---

## Phase 7: Scheduling — unattended daily run

**User stories**: 1, 42, 43

### What to build

Wrap the pipeline for hands-off operation: a single run command wired into **Windows Task Scheduler** to fire once daily at zero idle-run token cost, with "run task as soon as possible after a missed scheduled start" enabled so a powered-off/asleep machine self-heals on next boot.

### Acceptance criteria

- [ ] A documented Task Scheduler task runs the pipeline once daily against the live Inbox.
- [ ] Firing costs nothing while idle (no agent loop); the run is a plain script invocation.
- [ ] A missed scheduled run (machine off/asleep) executes at the next opportunity rather than skipping the day.

---

## Phase 8+: Template parsers, incrementally by volume

**User stories**: refines 38 (Template-first ladder) — cost optimization over the working AI fallback

### What to build

A **repeatable pattern**, not a fixed set: for each high-volume Source (ordered by the Phase 1 tally), obtain real sample Emails/PDFs, write a deterministic Template parser, golden-test it (one golden file per Source: sample → expected fields), and slot it into the Extraction Ladder **ahead of** the AI fallback. Concrete per-Source phases are enumerated once the Phase 1 volume data exists; the ladder already ships working on the AI fallback alone, so each Template is a pure cost reduction with no behavior change.

**Open dependency to resolve when this phase starts** (from the PRD): decide whether the developer reads sample Emails/PDFs live via the Gmail connector or exports samples per Source.

### Acceptance criteria (per Source slice)

- [ ] Real sample Email/PDF captured for the Source.
- [ ] Deterministic Template parser extracts the Source's fields with no AI call on the happy path.
- [ ] One golden file per Source (sample → expected extracted fields) passes.
- [ ] The parser is tried before the AI fallback; an unknown Source still falls through to AI unchanged.
- [ ] Verdicts/Buckets for that Source's Emails are unchanged vs. the AI-fallback baseline (behavior-preserving).
