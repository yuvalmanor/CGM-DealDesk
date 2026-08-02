"""Gmail work-queue query builder (pure).

The work queue is: Emails in the Inbox, received on/after the activation cutoff,
that do not yet carry a *terminal* Bucket label (i.e. not yet finished), and that
DealDesk did not send itself. Phase 1 uses this read-only for discovery; later
phases reuse it as the ingest queue.

A **retryable** Bucket (``Error``) is deliberately *not* negated, so an Email that
failed mid-run is folded back into the queue and retried on the next run (Phase 5).

**The self-ingestion exclusion.** DealDesk's Deal Notifications and Daily Digest
are sent *to* the deals mailbox *from* the deals mailbox (send-to-self, see
``notify.to``/``notify.from``). The delivered copy lands in the Inbox carrying no
Bucket label, so without this exclusion the next run picks its own output up as a
candidate deal — and because a Digest body lists every address in the run, the AI
rung happily re-extracts them all as "new" Properties. Left running from
2026-07-24, that loop wrote **316 of the Triage Log's 630 rows** and 12 of the 42
Calculator deals, and drove the "60 possible re-send" spikes in the daily logs.
The cure belongs *here*, in the queue definition: an Email that is never fetched
costs no read, no AI token, and can never reach a sheet.
"""

from __future__ import annotations

from datetime import date


def build_work_queue_query(
    cutoff: date,
    bucket_labels: tuple[str, ...] | list[str],
    retryable_labels: tuple[str, ...] | list[str] = (),
    self_addresses: tuple[str, ...] | list[str] = (),
) -> str:
    """Build the Gmail search query for the unprocessed work queue.

    ``after:`` uses Gmail's ``YYYY/MM/DD`` form and matches messages on/after that
    date. Each *terminal* Bucket label is negated so an already-filed Email is
    excluded; a label in ``retryable_labels`` (``Error``) is left un-negated so a
    failed Email stays in the queue for another attempt.

    ``self_addresses`` are the addresses DealDesk itself sends as (see
    ``Config.self_addresses``); each is negated with ``-from:`` so the pipeline
    never triages its own notifications. Nothing legitimate is lost: a Source
    cannot send as the deals mailbox, so mail from these addresses is DealDesk
    output by construction.
    """
    retry = set(retryable_labels)
    parts = ["in:inbox", f"after:{cutoff:%Y/%m/%d}"]
    for label in bucket_labels:
        if label in retry:
            continue  # retryable — leave it in the queue to retry next run
        parts.append(f"-label:{_quote_label(label)}")
    for address in _clean_addresses(self_addresses):
        parts.append(f"-from:{address}")
    return " ".join(parts)


def _clean_addresses(addresses: tuple[str, ...] | list[str]) -> list[str]:
    """De-duplicated, order-preserving, blanks dropped. A blank would emit a bare
    ``-from:`` that Gmail reads as junk — and, worse, could match nothing or
    everything depending on how it parses; drop it rather than guess."""
    seen: list[str] = []
    for raw in addresses:
        address = (raw or "").strip().lower()
        if address and address not in seen:
            seen.append(address)
    return seen


def _quote_label(label: str) -> str:
    # Gmail wants quotes around a label containing whitespace; our Bucket labels
    # don't, but quote defensively so an operator-renamed label can't break the
    # query.
    return f'"{label}"' if any(c.isspace() for c in label) else label
