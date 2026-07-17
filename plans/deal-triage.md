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

- [x] Each Property is evaluated independently; a multi-property Email's Bucket is the best Verdict (`Passed-BuyBox > Needs-Human > Rejected`); no Property → `Not-A-Deal`. _(Bucket Rollup + Orchestrator; `test_rollup`, `test_orchestrator` multi-property/Not-A-Deal, verified in the live spine drive.)_
- [x] Verdict matrix holds: present-&-fails-gate → Reject; missing filter field → Needs-Human; all gates pass → Pass; a confident Reject short-circuits missing data. _(`evaluator.py`; golden `test_evaluator` covers every cell incl. short-circuit.)_
- [x] Must-have fields are derived from the active Buy Box config (one config, not two); rent-to-price yield is never a rejection reason. _(`BuyBox.must_have_names`; `test_buybox`; `test_evaluator::test_low_yield_is_never_a_rejection_reason`.)_
- [ ] Facts are extracted from both email bodies and attached PDFs; unknown Sources still parse via the AI fallback. _(**Unchecked 2026-07-17**: verified against `text/plain` fixtures only. The Gmail Gateway collected `text/plain` parts alone, so an **HTML-only** Email — a lone `text/html` part, no plain alternative — reached extraction as its subject line and its body was silently dropped. That was 134 of 567 live candidate Emails (23.6%): `newwestern.com` (100) and `deals.diamondacquisitions.biz` (34). `ExtractionLadder`'s body+PDF gathering and the unknown-Source AI fallthrough were and remain correct — the gap was upstream, in what the gateway handed it. Closed by **Phase 8a**; re-check there against live HTML-only Sources.)_
- [x] Every Property is written as one Triage Log row with facts, Verdict, reasons, and Calc-ready flag; the upsert is idempotent on message-id + index. _(`triage_log.py` + `SheetsGateway`; `test_triage_log`, `test_sheets_gateway` idempotent-upsert.)_
- [x] The Bucket label is applied **last**; an Email is "done" only once labeled; a crash after the Triage write but before the label re-runs without duplicating the row (crash-recovery integration test passes). _(`Orchestrator` label-last; `test_orchestrator::test_crash_before_label_reruns_without_duplicating_row` passes.)_
- [x] Golden/table tests cover the Evaluator and Bucket Rollup decision matrices. _(`test_evaluator.py`, `test_rollup.py`.)_

---

## Phase 3: Calculator feed

**User stories**: 19, 20, 21, 22, 23, 24, 26, 36 (Calculator part)

### What to build

Add the downstream Calculator sink. The Deal Input Builder maps DealDesk Property fields to a **partial `Deal`** honoring the ADR-0002 contract, and the Sheets Gateway writes it to `DEALS_APP`. Feed a Property **iff Calc-ready AND Verdict is Pass or Needs-Human** — Reject is never fed. Feed-optional inputs (ARV) missing → still fed, written `arv: 0`. Store the created `DEALS_APP` row id on the Triage Log row and **update** it on re-run rather than appending. Fed deals inherit standing financing assumptions from the Calculator's own `DEFAULT_DEAL`.

### Acceptance criteria

- [x] A Calc-ready Pass or Needs-Human Property is written to `DEALS_APP`; a Reject is never written. _(`deal_input.should_feed` + Orchestrator feed; `test_orchestrator` calc-ready Pass/Needs-Human fed, Reject never fed; verified live via the feed drive.)_
- [x] A Property missing a feed-required input (e.g. rent) is not fed; one missing only a feed-optional input (ARV) is fed with `arv: 0`. _(`test_orchestrator::test_not_calc_ready_pass_is_not_fed`; `test_deal_input` arv:0 sentinel; live drive Case 2 fed with `arv:0`, Case 3 rent-missing not fed.)_
- [x] Every written row carries all five marker keys; `hmlLevPP`/`refiLtv` carry real model values (never `0`). _(`build_deal_input` always writes `MARKER_KEYS`; refuses a 0 assumption; `test_deal_input` marker/assumption golden + `test_calculator_gateway` row-schema; live drive shows all 5 keys, `hmlLevPP=69.565`/`refiLtv=65`.)_
- [x] The Triage Log row stores and links to the `DEALS_APP` row id; a re-run updates that row rather than creating a duplicate (idempotent feed). _(Deterministic `feed_row_id` stored on the Triage row; `test_orchestrator::test_calc_ready_pass_is_fed_and_triage_stores_row_id` + `test_rerun_does_not_duplicate_the_fed_deal`; `test_calculator_gateway` same-id updates in place.)_
- [x] Deal Input Builder golden tests cover marker-key presence, the `arv: 0` sentinel, real assumption markers, and the field mapping. _(`tests/test_deal_input.py`.)_

---

## Phase 4: Notifications — per-deal + Daily Digest

**User stories**: 29, 30, 31, 32, 33, 37

### What to build

The Notification Builder plus the Gmail Gateway **send** path (from `deals@cgm-ventures.com` so the operator's existing "Deal Notifications" filter routes them). Send one **Deal Notification** per `Passed-BuyBox` Property carrying address, Source, price/rent/ARV, Verdict + why, a link to the Calculator row, and any re-send flag — guarded by a **`notified`** flag on the Triage Log row so a retry never re-emails. Accumulate and send one **Daily Digest** per run: counts and one-line listings across all Buckets, with `Needs-Human` items listed alongside what's missing.

### Acceptance criteria

- [x] Exactly one Deal Notification per `Passed-BuyBox` Property; a re-run does not re-send (`notified` guard verified). _(Orchestrator reads `SheetsGateway.fetch_notified` and stamps the Triage row's `notified` flag; `test_orchestrator::test_passed_property_sends_one_notification_from_deals_mailbox` + `test_rerun_does_not_resend_notification`.)_
- [x] The notification carries address, Source, price/rent/ARV, Verdict + reasons, Calculator-row link, and re-send flag when present; it is sent from `deals@cgm-ventures.com`. _(`build_deal_notification`; `test_notifications` field/resend goldens; `test_gmail_gateway_full::test_send_message_encodes_from_subject_and_body` asserts the `From: deals@…` header.)_
- [x] Exactly one Daily Digest per run, with per-Bucket counts and one-line listings including `Rejected`/`Error`. _(`build_digest` + `Orchestrator._send_digest` (once in `run`); `test_run_sends_one_digest_covering_all_buckets`, `test_digest_counts_and_lists_all_buckets`.)_
- [x] `Needs-Human` Properties appear in the Digest with what's missing (no per-deal email for them). _(`test_digest_needs_human_shows_what_is_missing`; `test_needs_human_gets_no_per_deal_notification`.)_
- [x] Notification Builder golden tests cover per-deal and digest content, including the Needs-Human summary. _(`tests/test_notifications.py`.)_

---

## Phase 5: Error retry + age-based escalation

**User stories**: 10, 11

### What to build

Fold `Error`-labeled Emails back into the work queue so they retry on every daily run, and add age-based escalation: an `Error` Email older than ~3 days that keeps failing is relabeled `Needs-Human` (the Email's age stands in for a retry counter — no ledger).

### Acceptance criteria

- [x] `Error` Emails within the age window are picked up and retried on the next run; a transient failure that later succeeds ends in its correct terminal Bucket. _(Query no longer negates the retryable `Error` label — `build_work_queue_query(..., retryable_labels)` + `Orchestrator.run` derive it from `RETRYABLE_BUCKET_VALUES`; on success the terminal label strips the stale `Error` so the Email ends in exactly one Bucket. `test_query::test_retryable_label_is_not_negated`, `test_orchestrator::test_run_folds_error_back_into_work_queue` + `test_transient_failure_that_later_succeeds_ends_in_terminal_bucket`; verified live.)_
- [x] An `Error` Email older than ~3 days that still fails is escalated to `Needs-Human`. _(`Orchestrator._error_bucket` → `Needs-Human` when `age >= escalate_after_days`, stripping the `Error` label; `test_old_error_email_escalates_to_needs_human` + boundary test; live drive: 5-day-old failure → `Needs-Human`, `Error` removed.)_
- [x] Escalation uses Email age only; no retry-count ledger is introduced. _(Age derived from the `Date` header via `_email_age_days`; no counter/ledger anywhere — an unparseable date simply can't be aged and stays `Error`: `test_unparseable_date_cannot_be_aged_so_stays_error`.)_

---

## Phase 6: Re-send soft flag

**User stories**: 27, 28

### What to build

On ingest, run a cheap normalized-address lookup (Address Normalizer + a scan of prior Triage Log rows). If a prior Property matches, stamp the new row with a breadcrumb (e.g. "possible re-send — earlier row was `Rejected` @ $300k"). Rows are **never merged** and old rows are **never re-evaluated** — the flag exists only so a price-drop reviving a dead deal isn't missed.

### Acceptance criteria

- [x] A new Property whose normalized address matches a prior Triage Log row is stamped with a re-send breadcrumb referencing the earlier row's Verdict/price. _(`ResendIndex.lookup` + `build_resend_flag` → `TriageRow.resend_flag` via `build_triage_row`; `SheetsGateway.fetch_prior_properties` scans prior rows once per run. `test_orchestrator::test_matching_address_stamps_a_resend_breadcrumb_on_the_new_row`; drive: re-send row stamped `possible re-send — earlier row was Reject @ $400,000 on 2026-06-12`. The breadcrumb names the earlier row's **Verdict** (`Reject`) — the PRD's "`Rejected`" is the Bucket's name; per CONTEXT.md the row stores a Verdict.)_
- [x] Re-sends are recorded as new rows; no row is ever merged and no prior row is re-evaluated. _(The lookup is read-only — `PriorProperty` carries no write path, and the upsert only ever writes the current Email's rows. `test_resend_is_recorded_as_a_new_row_and_the_prior_row_is_untouched` (prior row byte-identical after), `test_a_resend_does_not_change_the_verdict`, `test_sheets_gateway::test_fetch_prior_properties_does_not_write`; drive run 2 keeps both rows, run 3 re-runs to 3 rows unchanged.)_
- [x] The breadcrumb surfaces in the Deal Notification (Phase 4) when present. _(Threaded orchestrator → `build_deal_notification(resend_flag=…)` rather than through `fields`, so `facts_json` stays a record of what the Source said: `test_the_breadcrumb_surfaces_in_the_deal_notification`, `test_the_breadcrumb_does_not_pollute_the_extracted_facts`; drive shows the `Re-send:` line carrying the $400,000 prior.)_
- [x] Address Normalizer golden tests cover equivalence ("St" ≡ "Street", unit suffixes, casing/whitespace) and non-equivalence (distinct houses stay distinct). _(`tests/test_address.py`, 42 cases: street types, directionals, state, casing/whitespace/punctuation, `Apt`≡`Unit`≡`#`; non-equivalence for number/name/street-type/directional/unit/city/zip. An address with nothing identifying (`"#"`, `"N St"`) yields the empty key, which never matches.)_

---

## Phase 7: Scheduling — the unattended-run artifacts

**User stories**: 42 (+ the build half of 1 and 43 — installing and observing them is Phase 9)

### What to build

Wrap the pipeline for hands-off operation: a single run command wired for **Windows Task Scheduler** to fire once daily at zero idle-run token cost, with "run task as soon as possible after a missed scheduled start" enabled so a powered-off/asleep machine self-heals on next boot.

**Scope note (2026-07-17):** this phase ships the *artifacts* — task definition, wrapper, installer, docs, tests. **Registering** the task on the operator's machine and verifying the two guarantees that only exist once Task Scheduler holds it moved to **Phase 9**, because the operator installs it themselves and neither claim can be honestly verified from this repo alone.

### Acceptance criteria

- [x] Firing costs nothing while idle (no agent loop); the run is a plain script invocation. _(The action is a single `Exec`: `powershell.exe -NoProfile -NonInteractive -File run-daily.ps1` — a process that starts, works, and exits, with no resident agent between runs. Driven live: the wrapper ran the pipeline and exited, propagating the pipeline's own exit code (0 on success, 2 on failure) so Task Scheduler's `LastTaskResult` is meaningful. `test_schedule::test_action_is_a_plain_script_invocation`.)_
- [x] The scheduling artifacts exist, are documented, and encode the daily + catch-up guarantees Phase 9 will verify. _(`scripts/dealdesk-daily.xml` (`DaysInterval=1`; `StartWhenAvailable=true`; `DisallowStartIfOnBatteries=false` so Windows' default doesn't silently re-skip the catch-up run on a laptop; `WakeToRun=false` per ADR-0001), `scripts/run-daily.ps1`, `scripts/install-task.ps1`, and a README "Scheduling" section. `install-task.ps1 -PrintOnly` verified live — resolves the real user/paths/start time and refuses an unsubstituted placeholder. `tests/test_schedule.py` (10 cases) pins each setting and cross-checks that the installer substitutes every placeholder the template declares.)_
- [x] The wrapper drives the real pipeline against the live Inbox and leaves a usable record. _(`run-daily.ps1 --dry-run --no-ai --limit 1` → 1 Email read, exit 0, no writes/tokens; a failing run propagates exit 2. Output is teed to `logs/dealdesk-<date>.log`, pruned after 30 days. UTF-8 is pinned across the redirection: piped rather than consoled, python fell back to the locale encoding and the run's em-dashes landed in the log as `U+FFFD` — verified fixed at the byte level (`E2 80 94`).)_

---

## Phase 8: Cheaper extraction, incrementally by volume

**User stories**: refines 38 (Template-first ladder) — cost optimization over the working AI fallback

**Enumerated 2026-07-17**, once the Phase 1 volume data existed (`dealdesk discover`: 567 candidate Emails in the window on/after 2026-06-28). Two findings reshaped this phase from its original "one slice per high-volume Source, ordered by the Phase 1 tally" sketch:

- **Domain ≠ Source, in both directions.** The tally keys on sender domain. `shared1.ccsend.com` (147) is Constant Contact's *shared* pool carrying 7 senders — 88% of them one wholesaler (LUSH, ~130). Conversely **Fazio spans two domains** (its own ccsend subdomain, 63, plus 2 via `shared1`). Refining the Phase 1 memo's caveat: Constant Contact encodes the sender's **real domain in the local part** (`dispo-lushinventory.com@shared1.ccsend.com`), so Source recognition can key on that rather than the subject/branding Phase 1 predicted. `derive_source` is domain-only today and will need this before any ccsend Source slice — but **not** for New Western.
- **~24% of volume had no body at all** (Phase 8a). For those Sources the "each Template is a pure cost reduction with **no behavior change**" premise was false: their baseline was a subject line, so any correct parser *must* change Verdicts. The behaviour-preserving criterion therefore applies only to slices whose baseline was sound.

Order **by Source** (not domain): LUSH ~130 (22.9%) · New Western 100 (17.6%) · Fazio 65 (11.5%). The tail below ~3% stays on the AI fallback indefinitely — that is the ladder working as designed, not a gap.

**Open dependency — resolved 2026-07-17** (was: read samples live via the Gmail connector, or export per Source?). Neither: a **dev-only capture command driven by the existing service-account `gmail.readonly` auth**, with the captured sample committed **verbatim** as the golden fixture. Reproducible for each successive Source slice, and keeps ADR-0001's "no MCP connectors" intact — the connector is a development convenience, not a sample pipeline. To be built in Phase 8b, the first slice that needs a fixture.

---

### Phase 8a: HTML-only Email bodies (prerequisite defect fix)

Not a Template slice. Split out on 2026-07-17 because the New Western Template could not be built — or honestly measured — on top of a defect that starved extraction for a quarter of live volume. Fixing it is a prerequisite for 8b/8c/8d, and it stands alone: it also repairs Diamond Acquisitions, which has no Template planned.

**What to build**: teach the Gmail Gateway to read `text/html` bodies. `text/plain` still wins when present (the parts of a `multipart/alternative` are the same content twice — concatenating both would double the AI rung's token cost for no gain); HTML is converted to text only when there is no usable plain part.

#### Acceptance criteria

- [x] An HTML-only Email's body reaches extraction as text; a `text/plain` Email is unchanged. _(`html_text.html_to_text` (stdlib `html.parser`; no new dependency) + `GmailGateway.fetch_email` plain-wins-else-HTML. `tests/test_html_text.py` (14 cases: table cells beside their label, rows to lines, script/style/head and `href` bulk dropped, entities/nbsp, mso conditional comments, whitespace collapse, malformed markup best-effort); `test_gmail_gateway_full` html-only, plain-wins, blank-plain-stub-falls-back, attached-`.html`-stays-an-attachment. Verified live: a New Western Email went from **31 chars (subject only) to 3,223**, Diamond Acquisitions from 54 to 777.)_
- [x] Facts the body carries are recovered from the real Sources. _(Live: New Western → `purchase_price 97500, year_built 1985, beds 2, baths 2.0`; Diamond → `279990 / 1958 / 5 beds / 4.0 baths` — each matching the Email by eye. A second New Western Email now yields a confident free `Reject` (`$475,000` fails the ≤350k gate) that previously could not be decided at all.)_
- [x] Exposing these bodies does not write worse data than the empty baseline did. _(Two pre-existing heuristics flaws that only *fired* once a body existed, both landing garbage in the Triage Log — the per-Property system of record: (1) the address label matched boilerplate (`NO UNACCOMPANIED ENTRY OF PROPERTY: Broker and its affiliates…`) and captured a paragraph of legalese, which then seeds the re-send lookup — a candidate must now carry a digit and be ≤100 chars, and every labeled candidate is scanned rather than only the first; (2) beds/baths read **value-first** (`3 bed / 2 bath`) while HTML table layouts are **label-first** (`Beds\n5`), so `$279,990\n\nBeds` yielded `beds: 990` and `baths: 5` where the truth was 4. Value-first now permits only horizontal space and cannot lop a count off a larger number; label-first is the fallback. Order matters — label-first against `3 bed / 2 bath` reads the baths number as beds. `tests/test_heuristics.py` pins both orientations against the real Source layouts verbatim.)_
- [x] The pipeline drives end-to-end against the live Inbox. _(`dealdesk run --dry-run --no-ai --limit 6 --verbose`: 6 Emails, no writes/tokens. New Western now files as a calc-ready `Needs-Human` carrying a real price; before it had no facts at all.)_
- [x] Phase 2's body-extraction criterion is re-verified and re-checked. _(Re-checked above against live HTML-only Sources.)_

**Recorded for 8b, not claimed here**: this slice buys **correctness, not yet cost**. The AI rung still fires for these Emails — the heuristics never recover `property_type`, a must-have — but it now reads a real body instead of a 31-char subject. The token saving arrives with the Templates, which recover `property_type` deterministically.

---

### Phase 8b: New Western Template · Phase 8c: LUSH · Phase 8d: Fazio

The **repeatable pattern**, one slice per Source, highest-volume first: capture a real sample Email/PDF, write a deterministic Template parser, golden-test it (one golden file per Source: sample → expected fields), and slot it into the Extraction Ladder **ahead of** the AI fallback.

**8b is New Western first**, ahead of higher-volume LUSH, deliberately: it is one domain with one format and uniform subjects, so domain-keyed `derive_source` already resolves it. That ships the repeatable pattern — capture command, parser registry, ladder rung, golden fixture — without also solving Source disambiguation. 8c (LUSH) then pays for the ccsend local-part keying and unifying `lushinventory.com` + `lushdeals.net`; 8d (Fazio) reuses it across its two domains.

Known Source traits to design against (observed live 2026-07-17): New Western **publishes no street address** — only city/state/ZIP (`Available - Granbury, TX, 76049`) — so its Properties cannot seed the re-send lookup, and `address` should stay absent rather than be faked. LUSH sends many **lots** and **out-of-market** (Corpus Christi) Properties, which the `property_type` gate rejects — a Template recovering `property_type` turns those into free, confident Rejects.

#### Acceptance criteria (repeated per Source slice)

- [ ] Real sample Email/PDF captured for the Source, via the dev-only capture command, committed verbatim as the fixture.
- [ ] Deterministic Template parser extracts the Source's fields with no AI call on the happy path.
- [ ] One golden file per Source (sample → expected extracted fields) passes.
- [ ] The parser is tried before the AI fallback; an unknown Source still falls through to AI unchanged.
- [ ] Verdicts/Buckets for that Source's Emails are unchanged vs. the **post-8a** AI-fallback baseline (behavior-preserving). _(Baseline must be re-measured after 8a; the pre-8a baseline is not a reference — see Phase 8a.)_

---

## Phase 9: Go live — install and verify the scheduled task

**User stories**: 1, 43 (the operator-gated half of Phase 7)

Split out of Phase 7 on 2026-07-17: the operator registers the task on their own machine, so these two guarantees have no running behavior to verify until that happens. Phase 7's artifacts are done and tested; nothing here is blocked on code.

### What to build

Probably nothing. This is the **operator action** Phase 7 deferred, plus verification against the registered task:

1. Set the credentials **user-scoped** (`setx GOOGLE_SERVICE_ACCOUNT_KEY …`, `setx ANTHROPIC_API_KEY …`) — a scheduled run does not inherit a shell's exported variables.
2. Register via `.\scripts\install-task.ps1` (`-At HH:mm` to choose the hour).
3. Verify the criteria below against Task Scheduler's own view of the task, not the template in this repo.

Fix anything registration surfaces in the **Phase 7 artifacts** rather than working around it here. Known risks worth watching: this is an Entra-joined machine, so the principal resolves to `AzureAD\<user>` — confirm Windows accepts it; and confirm the task's process actually sees the user-scoped env vars.

### Acceptance criteria

- [ ] A documented Task Scheduler task runs the pipeline once daily against the live Inbox. _(Verify on the registered task: `Get-ScheduledTaskInfo` shows a `NextRunTime` one day out and, after a real fire, `LastTaskResult = 0` with a matching `logs/dealdesk-<date>.log` and a Daily Digest in the Inbox.)_
- [ ] A missed scheduled run (machine off/asleep) executes at the next opportunity rather than skipping the day. _(The honest test is observational: leave the machine off across a scheduled time and confirm the run fires afterwards rather than being skipped. Note `LogonType=InteractiveToken` means "next opportunity" is next **logon**, not next boot — if that proves too weak in practice, the alternatives are a stored-password principal (`Password`/`S4U`) or ADR-0001's documented GitHub Actions escape hatch, both of which are **planning decisions to raise, not to make here**.)_
