"""Lightweight value types shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class MessageMeta:
    """Header-only view of an Email — enough to tally Sources without fetching
    bodies or attachments (keeps discovery cheap and strictly read-only)."""

    id: str
    from_addr: str
    subject: str
    date: str


@dataclass(frozen=True)
class Attachment:
    """A downloaded Email attachment. ``data`` is the raw bytes; the Extraction
    Ladder pulls text out of PDFs."""

    filename: str
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class Email:
    """A full Email — headers, plain-text body, and downloaded attachments —
    the unit the Orchestrator processes."""

    id: str
    from_addr: str
    subject: str
    date: str
    body_text: str
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)


class Verdict(str, Enum):
    """Per-Property Buy Box outcome (decided by filter-role fields only)."""

    PASS = "Pass"
    NEEDS_HUMAN = "Needs-Human"
    REJECT = "Reject"


class Bucket(str, Enum):
    """Email-level workflow state — an Email ends in exactly one Bucket (a Gmail
    label). ``Error`` is the only retryable Bucket."""

    PASSED_BUYBOX = "Passed-BuyBox"
    NEEDS_HUMAN = "Needs-Human"
    REJECTED = "Rejected"
    NOT_A_DEAL = "Not-A-Deal"
    ERROR = "Error"


# Error is the only retryable Bucket: unlike a terminal Bucket, an ``Error`` Email
# is *not* excluded from the work queue — it is folded back in every run so a
# transient failure retries (Phase 5). Age (not a ledger) governs when it escalates.
RETRYABLE_BUCKET_VALUES: tuple[str, ...] = (Bucket.ERROR.value,)
