# CGM DealDesk

Automated triage of inbound single-family-home investment "deals" for CGM Ventures (Dallas–Fort Worth / Texas market). Runs **once daily** as a local Python pipeline over an email inbox: extracts each property's facts from email text and attached property-report PDFs, filters them against a configurable investment Buy Box, routes each email into a workflow bucket represented by an inbox label, and writes qualifying deals into an existing BRRRR **Calculator** (Google Sheets) so underwriting results are waiting when the operator opens it. Records per-Property triage in Google Sheets; uses AI only as a last-resort extraction fallback. See [ADR-0001](docs/adr/0001-local-python-pipeline.md), [ADR-0002](docs/adr/0002-calculator-feed.md).

## Language

**Property**:
A single single-family home *as offered in one Email* — i.e. one **offer**. The atomic unit of evaluation; a "deal" *is* a Property. Identity is **per-offer**: the same physical house re-sent (price drop, or a second wholesaler) is a **new, distinct Property row**, never merged. Facts (address, asking price, estimated rent, beds/baths, etc.) are extracted per-Property.
_Avoid_: Deal (use only informally; "deal" = Property), listing, house, home. Note: Property ≠ the physical house — it is *this offer of* the house.

**Inbox**:
The dedicated, deals-only mailbox `deals@cgm-ventures.com` that DealDesk owns and watches. Not the operator's personal or RE-operations mailbox. DealDesk's entire territory; every unlabeled message in it is a candidate. Occasional spam/newsletters land in `Not-A-Deal`.
_Avoid_: mailbox (fine informally), account

**Email**:
A single inbound message in the Inbox from a wholesaler, agent, or MLS service. Carries the workflow state (the inbox label) and contains one or more Properties in its body and/or attached PDFs. Only Emails received on/after the **activation cutoff** are processed (older backlog is left untouched). Cutoff = **two weeks before activation** (i.e. 2026-06-28 for a 2026-07-12 start), configurable.
_Avoid_: Message, thread (a thread may matter later; for now the unit is the Email)

**Source**:
The wholesaler, agent, or MLS service an Email comes from, recognized by sender domain/subject. Most Sources send in a consistent format.
_Avoid_: Sender, vendor

**Template**:
A Source's recognizable email/PDF format, which maps to a deterministic parser. Extraction tries the matching Template's parser first; AI is only a last-resort fallback for fields it can't recover.
_Avoid_: Format, layout, parser (a Template *has* a parser)

**Buy Box**:
The configurable set of **gate** criteria a Property is tested against — property *attributes* only (location, price band, built-year, etc.). Yield is **not** a gate (see below). Pass/fail is evaluated per-Property.
_Avoid_: Criteria, filter, rules

**Field role** (Filter role × Calc role):
Every extracted field is tagged, in config, along two independent axes. **Filter role**: a field either *gates* the deal (built-year, location, price band) or doesn't. **Calc role**: a field is a **feed-required** calc input (must be present to feed the Calculator — e.g. rent), a **feed-optional** calc input (fed even if blank, then completed by hand in the Calculator — e.g. ARV), or not a calc input at all. A field can carry both roles: **price** gates the price band *and* is a feed-required calc input.

**Verdict**:
The per-Property outcome of Buy Box evaluation. Decided **only by filter-role fields**: filter field present & fails a gate → **Reject**; filter field missing → **Needs-Human**; all filter gates pass → **Pass**. A confident Reject short-circuits missing data.

**Calc-ready**:
A per-Property flag, independent of the Verdict, meaning **all feed-required calc inputs are present** so the Property can be fed to the Calculator. Feed-optional inputs (e.g. ARV) may be missing and the Property is *still* calc-ready — they're written blank for hand-completion. Missing a *feed-required* input (e.g. rent) → not calc-ready.

**Rent-to-price yield**:
A computed metric for ranking/underwriting — **never a rejection reason**. Market rent is only a proxy; CGM's strategy rents to Section 8 tenants, who typically pay well above market, so low market-rent yield does not imply a bad deal.
_Avoid_: treating yield as a filter or gate

**Calculator**:
The existing BRRRR deal-analysis app (`C:\YuvalManorPrivate\prod\brrrr-calculator`), which stores deals in a Google Sheet (`DEALS_APP` tab) and computes underwriting results live when a deal is opened. DealDesk *feeds* it; the Calculator is not part of DealDesk.
_Avoid_: model, underwriter, tool

**Deal input**:
The row DealDesk writes into the Calculator's `DEALS_APP` tab for a calc-ready Property — a *partial* `Deal` (extracted property fields + the assumption markers the guard requires), which the Calculator backfills from its own `DEFAULT_DEAL` model on open. ARV is always written (as `0` if unknown) to satisfy the Calculator's marker-key guard.
_Avoid_: deal row, save

**Triage Log**:
The Google Sheet tab (`DEALS_TRIAGE`) that is DealDesk's per-Property system of record — one row per Property with extracted facts, Verdict, reasons, Calc-ready, re-send flag, and a link to the `DEALS_APP` row it fed. Replaces the earlier Notion idea. Answers "which properties, what numbers, why."
_Avoid_: Notion, database, board

**Bucket** (Label):
An inbox label representing the workflow state of an **Email** (not a Property). Mutually exclusive: an Email ends in exactly one Bucket. For Emails containing Properties, the Bucket is a *rollup* of their Verdicts. The five Buckets:

| Bucket | Meaning | Terminal? |
|---|---|---|
| `Passed-BuyBox` | ≥1 Property passed the Buy Box | terminal |
| `Needs-Human` | no pass, but ≥1 Property un-evaluable (missing filter data) | terminal |
| `Rejected` | all Properties evaluated, none passed | terminal |
| `Not-A-Deal` | no Property found (spam, newsletter, reply) | terminal |
| `Error` | pipeline failed mid-processing | **retryable** |

`Error` is retried every daily run; if still failing and the Email is older than ~3 days, it escalates to `Needs-Human` (no retry counter — age stands in for attempts).

**Deal Notification**:
A per-Property email DealDesk sends for each **Passed-BuyBox** Property only (Needs-Human surfaces in the Daily Digest, not per-deal). Carries address, Source, price/rent/ARV, Verdict + reasons, a link to the Calculator row, and any re-send flag. **Sent from `deals@cgm-ventures.com`**, which the operator's existing Gmail filter already routes to the **"Deal Notifications"** label — no new filter needed. Sending is guarded by a **`notified` flag** on the Triage Log row so a retry never double-sends.
_Avoid_: alert, ping

**Daily Digest**:
One summary email per run: counts and one-line listings across all Buckets (incl. `Rejected`/`Error`), so the operator sees the run happened and what needs attention. Complements per-Property Deal Notifications.
_Avoid_: report, summary (fine informally)

## Relationships

- An **Email** contains one or more **Properties**
- A **Property** is evaluated against the **Buy Box** independently, producing one **Verdict**
- A **Property** is recorded as one row in the **Triage Log**
- An **Email** carries exactly one **Bucket** (label), a rollup of its Properties' Verdicts
- A **Property** carries a **Calc-ready** flag, independent of its Verdict
- A **calc-ready** Property with Verdict **Pass** is written as one **Deal input** in the **Calculator**

## Rollup & routing rules

- **Bucket rollup precedence** (email's Bucket = highest-ranking Property Verdict): `Passed-BuyBox` > `Needs-Human` > `Rejected`.
- **Calculator feed** (in scope): a Property is written as a **Deal input** iff it is **Calc-ready** *and* its Verdict is **Pass**. Neither **Reject** nor **Needs-Human** is fed — the Calculator holds only deals that cleared every Buy Box gate. The feed is **idempotent**: DealDesk stores the `DEALS_APP` row id on the Triage Log row and *updates* it on re-run rather than appending a duplicate.
- **Pass-only feed** (narrowed 2026-08-02; `Needs-Human` was feedable before): **Calc-ready and Pass are independent, and Calc-ready says nothing about the gates.** A gate field that is *feed-optional or unused by the Calculator* — `year_built` is both — can be missing while every feed-required input is present, which makes a Property calc-ready **and** Needs-Human at once. Since a *missing* gate field evaluates to Needs-Human (not Reject), such a Property was fed having never been checked against that gate at all: `dd-19f943b4faa18374-1` (2118 Stockton Trl, $230k) sat in `DEALS_APP` unmeasured against `year_built >= 2000`, because its Email never stated a year. `Needs-Human` remains a handoff bucket everywhere else — Triage row, label, Digest — it is just not entered into the Calculator until a human supplies the missing fact and it passes on the merits.
- **Same-offer guard** (added 2026-08-01): the row id is keyed on the Email, so it dedupes a *re-run* but not the same house arriving in a *new* Email — one $289k deal took nine `DEALS_APP` rows in the first live week. A Property whose **address and price both match** a Property already fed is therefore **not written again**; its Triage row links to the existing `DEALS_APP` row. A re-send at a **different price is a new offer and feeds normally** — that is the case the re-send flag exists for. Only the Calculator write is suppressed; the Triage row is always written (rows are never merged). An address-less Property is never deduped: two nameless deals cannot be proven to be one house.
- **Self-ingestion exclusion** (added 2026-08-01): Deal Notifications and the Daily Digest are sent *to* the mailbox DealDesk watches, so the work queue **excludes its own send addresses** (`inbox.address`, `notify.from`). Without it each run ingests the previous run's output as fresh deals — and a Digest body lists every address in the run, so the AI rung re-extracts them all. Left running for one week it wrote **316 of the Triage Log's 630 rows**.
- **Re-send soft flag**: on ingest, a cheap normalized-address lookup runs; if a prior Property row matches, the new row is stamped with a breadcrumb (e.g. "possible re-send — earlier row was `Rejected` @ $300k"). Rows are **never merged** and the old row is not re-evaluated — the flag exists only so a price-drop that revives a dead deal isn't missed.

## Stores, split responsibilities

All three stores are Google/Gmail — one ecosystem, one service-account auth (reused from the Calculator). **No Notion, no SQLite.**

- **Bucket (Gmail label)** = *email-level workflow state*. Guarantees idempotency ("this Email has been processed") and coarse triage. Cheap, inbox-native.
- **Triage Log (Google Sheet `DEALS_TRIAGE`)** = *per-Property system of record*. Makes "every Property ends in exactly one row" literally true and queryable. Carries each Property's facts, Verdict, Calc-ready, and re-send flag; also the idempotency backstop (message-id key) and the re-send lookup source.
- **Calculator (Google Sheet `DEALS_APP`)** = *downstream sink*, owned by the Calculator app. DealDesk writes calc-ready qualifying deals here as **Deal inputs**; it does **not** read results back (that's a later phase).

## Deferred (not yet decided)

- **Concrete Buy Box field catalog**: the full list of extracted fields, each field's **filter role** and **calc role** (feed-required / feed-optional / not-a-calc-input), and each gate's threshold. Year-built, rent, and ARV are *examples* used during design, not the final set. To be defined as config later.
- **Next phase**: reading Calculator results back to auto-score/notify on the genuinely attractive deals.

## Example dialogue

> **Dev:** "A wholesaler blasts one **Email** with five houses — one clears the **Buy Box**, four don't. What **Bucket**?"
> **Operator:** "The Email goes `Passed-BuyBox` — the rollup takes the best **Verdict**. But all five are still their own **Property** rows in the **Triage Log**; four say Reject, one says Pass."
>
> **Dev:** "That one Pass — but its **rent** is missing. Do we feed the **Calculator**?"
> **Operator:** "No — rent is feed-required, so it's not **Calc-ready**. It still passed the Buy Box though, so it's a `Passed-BuyBox` Property with no **Deal input**; I'll get the rent myself."
>
> **Dev:** "And if instead the **year-built** were missing?"
> **Operator:** "Different axis. Year is a filter field, so that Property is `Needs-Human`, not Pass. But rent and price are present, so it *is* Calc-ready — write the **Deal input** (with `arv: 0` if ARV's blank) so the numbers are waiting when I confirm the year."

## Flagged ambiguities

- "deal" was used to mean both the inbound Email and the individual house — resolved: a **Property** is the deal / unit of evaluation; the **Email** is merely its carrier.
- The invariant "every deal ends in exactly one bucket" was in tension with deal=Property + label-on-Email — resolved: *Emails* end in exactly one **Bucket**; *Properties* end in exactly one row in the **Triage Log**.
- "Notion" was the initial pick for the per-Property store — resolved: dropped in favour of a **Google Sheet tab** (`DEALS_TRIAGE`), to stay in one ecosystem with the Calculator and its existing service-account auth.
