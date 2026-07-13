"""Lightweight value types shared across the read half."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MessageMeta:
    """Header-only view of an Email — enough to tally Sources without fetching
    bodies or attachments (keeps discovery cheap and strictly read-only)."""

    id: str
    from_addr: str
    subject: str
    date: str
