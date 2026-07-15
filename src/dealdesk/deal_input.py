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

Also here: the **feed routing rule** (``should_feed``) and the **deterministic
row id** (``feed_row_id``) that makes the feed idempotent across a crash/retry.
"""

from __future__ import annotations

from dataclasses import dataclass

from .evaluator import Evaluation
from .models import Verdict

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

# Verdicts eligible for the Calculator feed. Reject is never fed.
_FEEDABLE_VERDICTS = (Verdict.PASS, Verdict.NEEDS_HUMAN)


@dataclass(frozen=True)
class Assumptions:
    """The standing financing assumptions DealDesk carries into every fed Deal —
    mirror the Calculator's ``DEFAULT_DEAL``. Both are marker keys the loader
    requires *and* must be real, non-zero values (ADR-0002)."""

    hml_lev_pp: float
    refi_ltv: float


def should_feed(evaluation: Evaluation) -> bool:
    """The Calculator-feed routing rule (CONTEXT.md): feed a Property iff it is
    **Calc-ready** *and* its Verdict is **Pass or Needs-Human**. Reject is never
    fed — the Calculator stays free of dead deals."""
    return evaluation.calc_ready and evaluation.verdict in _FEEDABLE_VERDICTS


def feed_row_id(message_id: str, index: int) -> str:
    """The ``DEALS_APP`` row id for a Property, derived deterministically from its
    Triage key ``(message-id, property index)``.

    Deterministic (rather than a random uuid) so the feed is idempotent by
    construction: a re-run recomputes the same id and *updates* the same row, and
    a crash between appending the row and recording its id can never duplicate the
    deal. The ``dd-`` prefix keeps it distinct from the Calculator's own uuids."""
    return f"dd-{message_id}-{index}"


def build_deal_input(fields: dict, assumptions: Assumptions) -> dict:
    """Build the partial ``Deal`` dict (the ``inputsJson`` payload) for a
    calc-ready Property. Callers feed only calc-ready Pass/Needs-Human Properties,
    so every feed-required input is present; feed-optional inputs (ARV, and now
    rent) may be missing and are written as the ``0`` sentinel."""
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
    }

    year = _num(fields.get("year_built"))
    if year is not None:
        deal["yearBuilt"] = year

    address = fields.get("address")
    if isinstance(address, str) and address.strip():
        deal["address"] = address.strip()

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
