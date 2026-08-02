"""Deal Input Builder (pure, deep module) — the ADR-0002 Calculator contract.

Maps a DealDesk Property's extracted fields to a **partial** Calculator ``Deal``
dict that DealDesk writes into the ``DEALS_APP`` tab. The Calculator's loader
(``parseSavedDeal``) merges this over its own ``DEFAULT_DEAL`` on open and
computes results live, so DealDesk only has to produce *valid inputs* — never the
~70 standing assumptions the Calculator owns.

The contract this module encodes (must not be broken):

- **Marker-key guard.** All five of ``purchasePrice, arv, monthlyRent, hmlLevPP,
  refiLtv`` must be present, or the Calculator silently rejects the row. They are
  always written.
- **ARV sentinel.** ARV is a *feed-optional* input often absent from wholesaler
  emails; the guard checks key-presence, not value, so a missing ARV is written
  as ``0`` ("unknown, fill in" — never "zero ARV").
- **Assumption markers carry real values.** ``hmlLevPP`` and ``refiLtv`` are
  assumption markers; because the merge lets the saved value win, a ``0``
  placeholder would zero out leverage and corrupt the math. DealDesk carries
  their true model values (from config, mirroring the Calculator's defaults) —
  never ``0``. These two are the only assumptions DealDesk carries.

Also here: the **feed routing rule** (``should_feed`` plus the same-offer guard
``already_fed``) and the **deterministic row id** (``feed_row_id``) that makes the
feed idempotent across a crash/retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from .evaluator import Evaluation
from .models import Verdict

if TYPE_CHECKING:  # type-only: keeps this pure module free of a runtime import
    from .resend import PriorProperty

# The five keys the Calculator's loader requires on every saved row (ADR-0002).
MARKER_KEYS = ("purchasePrice", "arv", "monthlyRent", "hmlLevPP", "refiLtv")

# DealDesk extracted-field name -> Calculator ``Deal`` key. Numeric fields are
# parsed; ``address`` is copied through. ``arv`` is handled specially (0
# sentinel), so it is not in this straight-through map.
FIELD_MAP = {
    "purchase_price": "purchasePrice",
    "monthly_rent": "monthlyRent",
    "year_built": "yearBuilt",
    "address": "address",
}

# Verdicts eligible for the Calculator feed: **Pass only** (narrowed 2026-08-02).
# Neither Reject nor Needs-Human is fed.
_FEEDABLE_VERDICTS = (Verdict.PASS,)


@dataclass(frozen=True)
class Assumptions:
    """The standing financing assumptions DealDesk carries into every fed Deal —
    mirror the Calculator's ``DEFAULT_DEAL``. Both are marker keys the loader
    requires *and* must be real, non-zero values (ADR-0002)."""

    hml_lev_pp: float
    refi_ltv: float


def should_feed(evaluation: Evaluation) -> bool:
    """The Calculator-feed routing rule (CONTEXT.md): feed a Property iff it is
    **Calc-ready** *and* its Verdict is **Pass** — a clean pass on every Buy Box
    gate. Reject and Needs-Human are both left out; the Calculator holds only
    deals that qualified.

    Needs-Human was feedable until 2026-08-02, on the reasoning that a deal the
    machine couldn't decide belongs in front of the operator with its numbers
    already computed. In practice that filled the Calculator with deals no gate
    had ever cleared: 2118 Stockton Trl (``dd-19f943b4faa18374-1``) sat there
    having never been checked against ``year_built >= 2000``, because the Email
    never stated a year and ``evaluate`` treats a *missing* gate field as
    Needs-Human rather than a Reject. Calc-ready says the Calculator's inputs are
    present; it says nothing about the gates, and ``year_built`` is a gate that is
    not feed-required, so the two can disagree exactly like that.

    The handoff is unchanged in every other respect — a Needs-Human Property still
    gets its Triage Log row, its bucket label, and its place in the Daily Digest.
    It just isn't entered into the Calculator until a human has supplied the
    missing fact and it passes on the merits."""
    return evaluation.calc_ready and evaluation.verdict in _FEEDABLE_VERDICTS


def already_fed(priors: Sequence["PriorProperty"], price: object) -> str:
    """The ``DEALS_APP`` row id of an earlier Property that is the **same offer**
    as this one, or ``""`` when the Calculator doesn't hold it yet.

    The second half of the feed routing rule, and the one that keeps the
    Calculator a *work surface* rather than a log. ``feed_row_id`` is keyed on
    (message-id, index), so it makes a re-run idempotent but says nothing about
    the same house arriving in a *different* Email — which is how 2002 Rockwall,
    TX 75032 @ $289,000 came to occupy nine ``DEALS_APP`` rows over the first
    live week.

    Same offer means **same address and same price**. The address alone is not
    enough, and deliberately so: the whole point of the re-send breadcrumb is
    that a price drop reviving a dead deal must not be missed (US 27/28), so a
    re-send at a *new* price is a new offer and feeds normally. Only the
    identical offer, arriving again, is suppressed.

    Note what this does **not** do. It never touches the Triage Log: the re-sent
    Property still gets its own row, with its breadcrumb — rows are never merged.
    It only declines to write a second Calculator row for a deal the Calculator
    already has, and hands back the existing id so the new Triage row and its
    notification still link to where the deal actually lives.

    ``priors`` comes from ``ResendIndex.lookup``, which refuses to key an
    unusable address, so an address-less Property can never be deduped against
    another — we cannot prove two nameless deals are one house, and a false
    merge is far worse than a duplicate row (see ``address.normalize_address``).
    """
    current = _num(price)
    if current is None:
        return ""  # no comparable price -> cannot claim it is the same offer
    for prior in reversed(priors):  # most recently recorded first
        if not prior.deals_app_row_id:
            continue  # never fed — nothing in the Calculator to collide with
        if _num(prior.price) == current:
            return prior.deals_app_row_id
    return ""


def feed_row_id(message_id: str, index: int) -> str:
    """The ``DEALS_APP`` row id for a Property, derived deterministically from its
    Triage key ``(message-id, property index)``.

    Deterministic (rather than a random uuid) so the feed is idempotent by
    construction: a re-run recomputes the same id and *updates* the same row, and
    a crash between appending the row and recording its id can never duplicate the
    deal. The ``dd-`` prefix keeps it distinct from the Calculator's own uuids."""
    return f"dd-{message_id}-{index}"


def build_deal_input(
    fields: dict, assumptions: Assumptions, address: str = "", seller_agent: str = ""
) -> dict:
    """Build the partial ``Deal`` dict (the ``inputsJson`` payload) for a
    calc-ready Property. Callers feed only calc-ready Pass Properties, so every
    feed-required input is present; feed-optional inputs (ARV, and now rent) may
    be missing and are written as the ``0`` sentinel.

    ``address`` is the caller's display label for the Property (see
    ``address.address_label``) — it carries the extracted address when there is
    one and the ``"<subject>|<sender>"`` fallback when there isn't, so a fed deal
    is never nameless in the Calculator. Omitted, the extracted field is used.

    ``seller_agent`` is who to call about the deal (see
    ``source.derive_seller_agent``). It comes from the Email's From header rather
    than ``fields``, because the Extraction Ladder only ever sees the Email's
    *text* — the sender is a header, so it can never appear in the extracted
    facts. Unlike every other optional input here it is written **even when
    empty**: the Calculator merges a saved deal over its ``DEFAULT_DEAL``, so an
    absent key inherits that example property's agent ("BSJ") and every triaged
    deal would claim a seller it never came from. An explicit ``""`` is the
    Calculator's own blank."""
    if not assumptions.hml_lev_pp or not assumptions.refi_ltv:
        # Guard the ADR-0002 invariant at the boundary: a 0 assumption marker
        # would pass the Calculator's key-presence guard but silently zero out
        # leverage. Refuse to build such a row.
        raise ValueError("assumption markers hmlLevPP/refiLtv must carry real, non-zero values")

    deal: dict = {
        "purchasePrice": _num(fields.get("purchase_price")),
        # Feed-optional (like ARV): missing/unparseable rent -> 0 sentinel, never
        # null. monthlyRent is a marker key, so it must always carry a value; 0
        # reads as "unknown, fill in" and the operator completes it in-app.
        "monthlyRent": _num(fields.get("monthly_rent"), default=0),
        # Feed-optional: missing/unparseable ARV -> 0 sentinel ("unknown, fill in").
        "arv": _num(fields.get("arv"), default=0),
        "hmlLevPP": assumptions.hml_lev_pp,
        "refiLtv": assumptions.refi_ltv,
        # Always present — see the docstring: an omitted key inherits the
        # Calculator's example seller, which would be a fabricated fact.
        "sellerAgent": seller_agent.strip() if isinstance(seller_agent, str) else "",
        # Same reasoning, and the same 0-means-unknown sentinel as ARV/rent: an
        # omitted key inherits the Calculator's example area (1,621 sq ft), so a
        # deal with no stated size would silently claim one.
        "sqft": _num(fields.get("sqft"), default=0),
    }

    year = _num(fields.get("year_built"))
    if year is not None:
        deal["yearBuilt"] = year

    label = address.strip() if isinstance(address, str) else ""
    if not label:
        raw = fields.get("address")
        label = raw.strip() if isinstance(raw, str) else ""
    if label:
        deal["address"] = label

    return deal


def _num(value: object, default: float | None = None) -> float | int | None:
    """Parse an extracted value to a number, tolerating ``$``/``,`` formatting.
    Returns ``default`` when missing or unparseable. Whole numbers come back as
    ``int`` so the JSON payload reads like a hand-entered deal (250000, not
    250000.0)."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return _demote(value)
    if value is None:
        return default
    text = str(value).strip().lstrip("$").replace(",", "").strip()
    try:
        return _demote(float(text))
    except ValueError:
        return default


def _demote(n: float) -> float | int:
    return int(n) if float(n).is_integer() else n
