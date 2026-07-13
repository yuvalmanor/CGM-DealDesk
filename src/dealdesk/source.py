"""Source derivation (pure).

A Source is the wholesaler/agent/MLS service an Email comes from, recognized by
sender domain. Phase 1 keys the Pareto tally on the sender domain; subject-based
disambiguation is left to the later Template phases that actually need it.
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
