"""Gmail work-queue query builder (pure).

The work queue is: Emails in the Inbox, received on/after the activation cutoff,
that do not yet carry a Bucket label (i.e. not yet processed). Phase 1 uses this
read-only for discovery; later phases reuse it as the ingest queue.
"""

from __future__ import annotations

from datetime import date


def build_work_queue_query(cutoff: date, bucket_labels: tuple[str, ...] | list[str]) -> str:
    """Build the Gmail search query for the unprocessed work queue.

    ``after:`` uses Gmail's ``YYYY/MM/DD`` form and matches messages on/after that
    date. Each Bucket label is negated so an already-filed Email is excluded.
    """
    parts = ["in:inbox", f"after:{cutoff:%Y/%m/%d}"]
    for label in bucket_labels:
        parts.append(f"-label:{_quote_label(label)}")
    return " ".join(parts)


def _quote_label(label: str) -> str:
    # Gmail wants quotes around a label containing whitespace; our Bucket labels
    # don't, but quote defensively so an operator-renamed label can't break the
    # query.
    return f'"{label}"' if any(c.isspace() for c in label) else label
