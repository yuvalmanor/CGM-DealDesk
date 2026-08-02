"""Source derivation (pure).

A Source is the wholesaler/agent/MLS service an Email comes from, recognized by
sender domain. Phase 1 keys the Pareto tally on the sender domain; subject-based
disambiguation is left to the later Template phases that actually need it.

Also here: ``derive_seller_agent`` — the same From header read as a *person or
company to call*, which is what the Calculator's ``sellerAgent`` field wants.
"""

from __future__ import annotations

from email.utils import parseaddr

UNKNOWN_SOURCE = "unknown"


def derive_source(from_addr: str, subject: str = "") -> str:
    """Return the Source key for an Email — its lowercased sender domain.

    Falls back to ``unknown`` when the From header carries no parseable address.
    ``subject`` is accepted for a stable signature as later phases refine Source
    recognition, but is unused in Phase 1.
    """
    _, addr = parseaddr(from_addr or "")
    _, _, domain = addr.partition("@")
    domain = domain.strip().lower()
    return domain or UNKNOWN_SOURCE


def derive_seller_agent(from_addr: str) -> str:
    """Who is offering this deal — the Calculator's ``sellerAgent``, read straight
    off the From header.

    The display name when the sender has one ("Momentum Capital", "Reed Hunter"),
    otherwise the bare address. The display name is what the operator would
    actually say when asked who to call; the address is the honest fallback,
    because a blank tells them nothing and this field is never a key — it is a
    label a human reads.

    Not lowercased (unlike ``derive_source``): this is a proper name shown in the
    Calculator's UI, not a match key.
    """
    name, addr = parseaddr(from_addr or "")
    return name.strip() or addr.strip()
