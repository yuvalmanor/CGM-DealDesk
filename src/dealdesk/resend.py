"""Re-send soft flag (pure, deep module).

A *soft breadcrumb*, not a dedup. On ingest each new Property's normalized
address is looked up against the Properties already recorded in the Triage Log;
a match stamps the new row with e.g.

    possible re-send — earlier row was Rejected @ $300,000 on 2026-06-12

and stops there. Two rules from the PRD (US 27/28) shape everything below:

- **Rows are never merged.** A re-send is a new Property row, always. Identity is
  per-offer (see CONTEXT.md) — the same house re-sent at a lower price is a new
  offer, and the whole point of the flag is that a price drop reviving a dead
  deal isn't missed.
- **Prior rows are never re-evaluated.** The lookup is read-only. An earlier
  ``Rejected`` row stays Rejected; the breadcrumb only *reports* what it said.

The flag deliberately says "possible": the address key is spelling-level
evidence, not proof, and the operator makes the call.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Sequence

from .address import normalize_address


@dataclass(frozen=True)
class PriorProperty:
    """A Property already recorded in the Triage Log — the re-send lookup's view
    of an earlier row. Read-only by construction: it carries no way to write
    back."""

    message_id: str
    property_index: int
    address: str
    verdict: str
    price: float | None = None
    received_date: str = ""

    @property
    def key(self) -> tuple[str, int]:
        """Mirrors the Triage Log's (message-id, index) identity."""
        return (self.message_id, self.property_index)


class ResendIndex:
    """Normalized address -> the prior Properties recorded at it.

    Built once per run from the Triage Log's existing rows, then updated in
    memory as the run records new ones — so two Emails in the *same* run offering
    the same house also cross-flag, at no extra read.
    """

    def __init__(self, priors: Sequence[PriorProperty] = ()):
        self._by_key: dict[str, list[PriorProperty]] = {}
        for prior in priors:
            self.add(prior)

    def add(self, prior: PriorProperty) -> None:
        key = normalize_address(prior.address)
        if not key:
            return  # no key -> unmatchable; never claim two blanks are one house
        bucket = self._by_key.setdefault(key, [])
        # Re-recording the same Property (a re-run) replaces its entry rather
        # than stacking a duplicate — the index mirrors the Triage Log's key.
        for i, existing in enumerate(bucket):
            if existing.key == prior.key:
                bucket[i] = prior
                return
        bucket.append(prior)

    def lookup(self, address: object, exclude_message_id: str) -> list[PriorProperty]:
        """Prior Properties at ``address``, excluding the Email being processed.

        Excluding its own message-id is what makes a re-run idempotent: on the
        second pass the Email's own rows are already in the Triage Log, and
        without this it would flag itself as a re-send of itself. It also stops
        one Email that lists the same house twice from self-flagging.
        """
        key = normalize_address(address)
        if not key:
            return []
        return [p for p in self._by_key.get(key, []) if p.message_id != exclude_message_id]


def build_resend_flag(priors: Sequence[PriorProperty]) -> str:
    """The breadcrumb for a Property whose address matched ``priors``. Empty
    string when there's no match — the overwhelmingly common case."""
    if not priors:
        return ""

    # Sheet order is run order, so the last match is the most recently recorded.
    latest = priors[-1]
    text = f"possible re-send — earlier row was {latest.verdict or 'unrecorded'}"
    price = _money(latest.price)
    if price:
        text += f" @ {price}"
    when = _received_on(latest.received_date)
    if when:
        text += f" on {when}"
    if len(priors) > 1:
        text += f" ({len(priors)} earlier rows)"
    return text


def _money(value: float | None) -> str:
    return "" if value is None else f"${value:,.0f}"


def _received_on(raw: str) -> str:
    """The earlier row's received date as ``YYYY-MM-DD``. An Email whose Date
    header won't parse simply contributes no date to the breadcrumb."""
    if not raw:
        return ""
    try:
        received = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return ""
    return f"{received:%Y-%m-%d}" if received else ""
