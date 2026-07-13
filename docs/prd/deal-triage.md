# PRD: DealDesk Triage Pipeline — daily inbox triage + Calculator feed

**Feature:** `deal-triage`
**Status:** ready-for-agent
**Related:** [CONTEXT.md](../../CONTEXT.md), [ADR-0001](../adr/0001-local-python-pipeline.md), [ADR-0002](../adr/0002-calculator-feed.md)

## Problem Statement

I'm a single-family-home investor (CGM Ventures, Dallas–Fort Worth). Wholesalers, agents, and MLS services flood my dedicated `deals@cgm-ventures.com` inbox with property "deals" — in email bodies and attached property-report PDFs, each in a different format. Today I open every one by hand, pull out the numbers, sanity-check them against my buying criteria, and re-type the survivors into my BRRRR Calculator before I can even see whether a deal is worth pursuing. It's slow, it's error-prone, and good deals slip past me — especially **price-drop re-sends** of a house I passed on last month, which now pencils. I also burn time on deals that are plainly out of my box (wrong area, too old, overpriced) and on junk mail. I want the boring triage done for me, cheaply, so that when I sit down the good deals are already sorted and already loaded into my Calculator.

## Solution

A local pipeline runs **once a day** over the `deals@cgm-ventures.com` **Inbox**. For each **Email**, it extracts every **Property**, evaluates each against my configurable **Buy Box**, and files the Email into exactly one **Bucket** (a Gmail label) so nothing is ever processed twice and every Email ends up in exactly one place. Every Property is recorded as a row in a **Triage Log** (a Google Sheet), and every qualifying, **Calc-ready** Property is written as a **Deal input** into my Calculator's Google Sheet — so underwriting results are waiting when I open the Calculator, even if I still have to fill in something like ARV by hand. I get a **Deal Notification** email for each deal worth looking at and one **Daily Digest** summarizing the run. It's deliberately cheap: extraction tries a deterministic **Template** parser first and only falls back to AI when it must.

## User Stories

1. As an investor, I want the pipeline to run automatically once a day, so that I don't have to remember to trigger it.
2. As an investor, I want it to watch only my dedicated `deals@cgm-ventures.com` Inbox, so that my personal and RE-operations mail are never touched.
3. As an investor, I want only Emails received on/after an activation cutoff (default two weeks back) processed, so that months of stale backlog aren't swept in on the first run.
4. As an investor, I want to be able to move the cutoff deliberately, so that I can sweep older backlog when I choose to.
5. As an investor, I want each Email filed into exactly one Bucket, so that my Inbox becomes a clean, at-a-glance workflow board.
6. As an investor, I want an Email with at least one passing Property labeled `Passed-BuyBox`, so that I can find the winners instantly.
7. As an investor, I want an Email whose Properties all cleanly fail labeled `Rejected`, so that I can ignore them with confidence.
8. As an investor, I want an Email with an un-evaluable Property (missing filter data) labeled `Needs-Human`, so that I know it needs my eyes.
9. As an investor, I want spam/newsletters/replies labeled `Not-A-Deal`, so that they're not re-examined every run.
10. As an investor, I want transient failures labeled `Error` and retried, so that a network blip doesn't lose a deal.
11. As an investor, I want an `Error` Email that keeps failing for ~3 days escalated to `Needs-Human`, so that genuinely broken items reach me instead of retrying forever.
12. As an investor, I want each Property evaluated independently against the Buy Box, so that one bad house in a multi-property Email doesn't sink the good one.
13. As an investor, I want a multi-property Email's Bucket to reflect its best Property (`Passed-BuyBox` > `Needs-Human` > `Rejected`), so that a single winner still surfaces the whole Email.
14. As an investor, I want a Property whose present data fails a gate (e.g. built 1960 vs. a >2000 rule) marked `Reject`, so that confident no's are auto-filed.
15. As an investor, I want a Property missing a filter field (e.g. year unknown) marked `Needs-Human`, so that I can look the value up rather than lose the deal.
16. As an investor, I want a confident Reject to short-circuit missing data, so that an already-dead deal doesn't waste a human review slot.
17. As an investor, I want which fields are "must-have" derived from my active Buy Box (core-metric inputs + fields I actually filter on), so that I maintain one config, not two.
18. As an investor, I want rent-to-price yield treated as a ranking metric and never a rejection reason, because my Section 8 strategy means market rent understates real income.
19. As an investor, I want every qualifying Calc-ready Property written into my Calculator automatically, so that results are waiting when I open it.
20. As an investor, I want a Property fed to the Calculator only when its feed-required inputs (e.g. rent) are present, so that I don't get meaningless half-computed rows.
21. As an investor, I want a Property still fed when a feed-optional input like ARV is missing (written blank), so that I can fill ARV in the Calculator and see results immediately.
22. As an investor, I want `Needs-Human` deals also fed to the Calculator when Calc-ready, so that after I confirm the missing filter value the numbers are already there.
23. As an investor, I want `Rejected` deals never fed to the Calculator, so that it stays free of dead deals.
24. As an investor, I want the fed Deal input to inherit my standing financing assumptions from the Calculator's own defaults, so that I never re-enter HML/refi/threshold numbers.
25. As an investor, I want each Property recorded in the Triage Log with its facts, Verdict, reasons, and Calc-ready flag, so that I can see which properties, what numbers, and why.
26. As an investor, I want the Triage Log row to link to the Calculator row it fed, so that I can jump from triage to underwriting.
27. As an investor, I want a possible re-send flagged with a breadcrumb (e.g. "earlier row was `Rejected` @ $300k"), so that a price-drop reviving a dead deal isn't missed.
28. As an investor, I want re-sends recorded as new rows and never silently merged, so that the pipeline never fabricates a "same house" claim it can't be sure of.
29. As an investor, I want a Deal Notification email for each `Passed-BuyBox` Property, so that winners actively reach me.
30. As an investor, I want that notification to carry address, Source, price/rent/ARV, the Verdict and why, a link to the Calculator row, and any re-send flag, so that I can judge it without opening anything else.
31. As an investor, I want Deal Notifications sent from `deals@cgm-ventures.com`, so that my existing "Deal Notifications" Gmail filter routes them without a new rule.
32. As an investor, I want one Daily Digest email per run with counts and one-line listings across all Buckets, so that I see the run happened and what needs attention.
33. As an investor, I want `Needs-Human` items listed in the Daily Digest with what's missing, so that I can act on them even though they don't get a per-deal email.
34. As an investor, I want the pipeline to never process the same Email twice, so that I don't get duplicate rows or duplicate emails.
35. As an investor, I want an Email considered "done" only once it carries a Bucket label, so that a crash mid-run is safely re-processed next run.
36. As an investor, I want Triage Log and Calculator writes to be idempotent (keyed on message-id / stored row id), so that a re-run updates rather than duplicates.
37. As an investor, I want a Deal Notification sent at most once per Property (a `notified` guard), so that a retry never re-emails me.
38. As an investor, I want extraction to try a deterministic Template parser first and use AI only as a last resort for missing critical fields, so that the tool stays cheap.
39. As an investor, I want unknown Sources to still work via the AI fallback, so that a brand-new wholesaler's mail isn't dropped just because it has no Template yet.
40. As an investor, I want facts extracted from both email bodies and attached PDFs, so that data in a property-report PDF isn't ignored.
41. As an operator, I want the Buy Box (fields, roles, thresholds) defined in config, so that I can tune my criteria without code changes.
42. As an operator, I want the pipeline triggered by Windows Task Scheduler, so that there is zero idle-run token cost.
43. As an operator, I want a missed daily run to execute at next opportunity, so that the machine being asleep at the scheduled time doesn't skip a day.
44. As an operator, I want all state in Google/Gmail with one service-account auth, so that there's no extra store or account to manage.

## Implementation Decisions

**Architecture (per [ADR-0001](../adr/0001-local-python-pipeline.md)).** A local **Python** pipeline, triggered by **Windows Task Scheduler once daily**, talking directly to the Gmail API and Google Sheets API. Not a Claude agent; no MCP connectors in production. AI (Anthropic API) is a last-resort extraction fallback only.

**Calculator integration (per [ADR-0002](../adr/0002-calculator-feed.md)).** DealDesk writes a **partial `Deal`** row into the Calculator's `DEALS_APP` Google Sheet tab; the Calculator backfills standing assumptions from its own `DEFAULT_DEAL` on open and computes results live. The write must satisfy the Calculator's marker-key guard (`purchasePrice, arv, monthlyRent, hmlLevPP, refiLtv` all present); ARV is written as `0` when unknown; assumption markers (`hmlLevPP`, `refiLtv`) carry real values, never `0`.

**Stores.** Three, all Google/Gmail, one service-account auth:
- **Gmail labels** = email-level workflow state (the Bucket).
- **Triage Log** = Google Sheet tab `DEALS_TRIAGE`, per-Property system of record and the idempotency + re-send-lookup backstop.
- **Calculator** = Google Sheet tab `DEALS_APP`, downstream sink (write-only; results are not read back this phase).

**Deep modules (stable interfaces, no I/O):**
- **Buy Box Evaluator** — `(property_fields, buybox_config) → Verdict + reasons + calc_ready + missing_fields`. Filter-role fields decide the Verdict (present-and-fails → Reject; missing → Needs-Human; else Pass; Reject short-circuits). Calc-ready = all feed-required calc inputs present.
- **Bucket Rollup** — `[Verdict…] → Bucket` with precedence `Passed-BuyBox > Needs-Human > Rejected`; empty (no Property) → `Not-A-Deal`.
- **Deal Input Builder** — `property_fields → partial Deal dict` encoding the ADR-0002 contract (marker keys, `arv: 0` sentinel, assumption markers, DealDesk→Calculator field mapping).
- **Address Normalizer** — `raw_address → normalized_key` (pure), used by the re-send lookup.
- **Extraction Ladder** — `Email → [property_fields]`, internally Template parsers → generic heuristics → AI fallback (fallback behind an interface so it can be faked). Sub-parts: per-Source **Template** parsers, **PDF text extractor**, **AI fallback client**.
- **Notification Builder** — `(property, bucket, links) → email content` for per-deal and digest.

**Thin I/O adapters:**
- **Gmail Gateway** — fetch work queue (unlabeled + `Error`-within-age, since activation cutoff), apply Bucket label (label-last), send mail from `deals@cgm-ventures.com`.
- **Sheets Gateway** — Triage Log upsert keyed on Gmail message-id (+ property index); `DEALS_APP` write/update keyed on stored row id.

**Orchestrator.** Per Email, enforce ordering: extract → evaluate each Property → rollup → **upsert Triage Log** → **feed Calculator** (Calc-ready + Pass/Needs-Human) → **apply Bucket label last** → send Deal Notifications / accumulate Digest (guarded by the `notified` flag). Label-last + idempotent upserts guarantee "nothing processed twice" across a crash.

**Error handling.** Mid-run failure → `Error` label; retried every daily run; if still failing and the Email is older than ~3 days, escalate to `Needs-Human` (age stands in for a retry counter — no ledger).

**Triage Log schema (indicative columns).** message-id, property index, received date, Source, address, extracted facts, Verdict, reasons, calc-ready, missing fields, re-send flag, `DEALS_APP` row id, `notified` flag.

**Config.** The concrete Buy Box field catalog — each field's filter role and calc role (feed-required / feed-optional / not-a-calc-input) and each gate's threshold — lives in a config file, defined by the operator (not hard-coded).

## Testing Decisions

**What makes a good test here:** exercise **external behavior** through a module's public interface, not its internals. For the pure modules that means table/golden-style cases: given a set of extracted fields (or a sample email) and a config, assert the resulting Verdict / Bucket / Deal-input dict / normalized key — never assert on private helpers or call order.

**Modules under test (the pure set):**
- **Buy Box Evaluator** — golden cases across the decision matrix: present-and-fails → Reject; missing filter field → Needs-Human; all-pass → Pass; Reject short-circuits missing data; feed-required missing → not Calc-ready; feed-optional (ARV) missing → still Calc-ready.
- **Bucket Rollup** — precedence and the mixed-Email cases (one Pass among Rejects → `Passed-BuyBox`; no Pass + one Needs-Human → `Needs-Human`; all Reject → `Rejected`; no Property → `Not-A-Deal`).
- **Deal Input Builder** — marker keys always present; `arv: 0` when unknown; assumption markers carry real values (never `0`); DealDesk→Calculator field mapping correct.
- **Address Normalizer** — equivalence cases ("123 Main St" ≡ "123 Main Street"; unit suffixes; casing/whitespace) and non-equivalence (distinct houses stay distinct).
- **Template parsers** — one golden file per Source: sample Email/PDF → expected extracted fields.
- **Notification Builder** — per-deal and digest content assertions (fields present, Needs-Human summarized).

**Lighter coverage:** Gmail and Sheets gateways get integration tests against mocked Google APIs (queue query shape, label-last, idempotent upsert). The Orchestrator gets one happy-path and one **crash-recovery** integration test (crash after Triage write, before label → re-run does not duplicate).

**Prior art:** the sibling `brrrr-calculator` repo uses golden-file tests for its deal model and saved-deal parser (`tests/deal-model.golden.test.ts`, `tests/parse-saved-deal.test.ts`). Mirror that golden-file discipline in Python (pytest), especially for Template parsers and the Buy Box Evaluator.

## Out of Scope

- **Next phase — reading Calculator results back** to auto-score and rank the *genuinely attractive* deals, and any per-deal "this one's a strong buy" alert built on those results. This phase only feeds inputs; it does not read outputs.
- **True address-based dedup / merge.** Only a soft re-send breadcrumb; rows are never merged and old rows are never re-evaluated.
- **Real-time / push ingestion.** Daily batch only; Gmail push/webhooks not built.
- **Cloud scheduling (GitHub Actions) and a SQLite ledger** — documented escape hatches, not built.
- **Building all Source Templates up front.** Templates are added incrementally, by volume, in later phases; the ladder ships working on the AI fallback alone.
- **The Calculator app itself** and its financial model — owned by `brrrr-calculator`; DealDesk only writes inputs to its sheet.
- **Section 8 rent modeling** — yield is informational; no Section 8 rent estimation this phase.

## Further Notes

- **Phasing philosophy (load-bearing):** Phase 1 is a **template-free tracer bullet** — the full spine end-to-end using generic + AI fallback only, no Source-specific parsers. An early discovery step samples the activation window of the Inbox to see which Sources dominate (expect Pareto). Template parsers are then added incrementally, highest-volume Sources first, each golden-tested. (Recorded as a project memory so `/plan-phases` honors it.)
- **Sample-access dependency:** building any Template needs real sample Emails/PDFs. Decide at planning time whether the developer reads the Inbox live via the Gmail connector or exports samples per Source.
- **Config to be authored:** the concrete Buy Box field catalog (fields, roles, thresholds) is deferred to a config exercise; year-built, rent, and ARV are design examples, not the final set.
- **Guarantees to preserve throughout:** every Email ends in exactly one Bucket; nothing is processed twice (label-last + message-id upsert); no side effect (Calculator row, notification email) is duplicated on retry.
