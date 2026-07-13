# 0002 — Feed the BRRRR Calculator by writing partial Deal rows to its Google Sheet

**Status:** accepted

## Decision

DealDesk feeds qualifying deals into the existing BRRRR Calculator (`C:\YuvalManorPrivate\prod\brrrr-calculator`) by **writing a row directly to its `DEALS_APP` Google Sheet tab**, reusing the Calculator's Google service-account auth. The written `inputsJson` is a **partial `Deal`**: only the fields DealDesk extracted, plus the marker keys the Calculator's loader requires. The Calculator backfills all standing assumptions from its own `DEFAULT_DEAL` when the deal is opened. DealDesk does **not** read results back — that is a later phase.

This also settles the per-Property record store: it is a sibling Google Sheet tab (`DEALS_TRIAGE`), **not Notion** — one ecosystem, one auth.

## Why

- **The Calculator already owns the financial model.** [`parseSavedDeal`](../../../prod/brrrr-calculator/lib/parse-saved-deal.ts) merges saved inputs over `DEFAULT_DEAL` (`{ ...DEFAULT_DEAL, ...saved }`). Writing a partial deal means DealDesk never duplicates ~70 HML/refi/threshold assumptions; if the operator tweaks `DEFAULT_DEAL`, fed deals inherit the change for free.
- **Results are computed on open, so DealDesk needn't run the TS engine.** It only has to produce valid inputs; the Calculator computes score/ARV/NOI/etc. live. This is exactly the operator's goal: "open the tool and the results are already there."
- **One ecosystem.** The Calculator runs on Google Sheets with a service account; the triage record and the feed both reuse it. No Notion account/API, consistent with the cost constraint (ADR-0001).

## Constraints imposed by the Calculator's loader (must not be broken)

- **Marker-key guard.** `DEAL_MARKER_KEYS = [purchasePrice, arv, monthlyRent, hmlLevPP, refiLtv]`. If **any** key is absent, the row is rejected (`parseSavedDeal` returns `null`) and silently won't load. DealDesk must always write all five keys.
- **ARV sentinel.** ARV is a *feed-optional* input often absent from wholesaler emails. The guard checks key-presence, not value, so DealDesk writes **`arv: 0`** when unknown — it passes the guard, shows as an empty field, and the operator fills it in-app.
- **Assumption markers must carry real values.** `hmlLevPP` and `refiLtv` are assumption markers. Because the merge lets the saved value win, writing `0` as a placeholder would **zero out leverage and silently corrupt the math**. DealDesk writes their true model values, never placeholders. (These two are the only assumptions DealDesk carries.)
- **Idempotent feed.** DealDesk stores the created `DEALS_APP` row `id` on the Triage Log row and *updates* that row on re-run rather than appending — so a crash-and-retry never duplicates a deal in the Calculator.

## Consequences

- DealDesk is coupled to the Calculator's `DEALS_APP` schema and marker-key contract. A change to either side's field names must be coordinated. This ADR is the coordination point.
- A future reader who sees `arv: 0` in a fed row should read it as "unknown, fill in," not "zero ARV."
