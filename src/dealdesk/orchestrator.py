"""Orchestrator — the per-Email triage spine.

For each Email, enforce the ordering that guarantees the two headline invariants:

    extract -> evaluate each Property -> rollup -> upsert Triage Log
        -> feed Calculator -> label LAST

Because the Triage Log upsert is idempotent (keyed on message-id + index) and the
Bucket label is applied *last*, a hard crash after the upsert but before the
label leaves the Email unlabeled — it's re-processed next run and the upsert
updates rather than duplicates. A *handled* mid-run failure lands the Email in
``Error`` (retryable).

The Calculator feed (Phase 3) sits between the Triage upsert and the label. A
calc-ready Pass/Needs-Human Property is written as a partial Deal into
``DEALS_APP``, keyed on a deterministic row id (``feed_row_id``) so the feed is
idempotent by construction and the Triage row can carry the id as the
triage->Calculator link. Notifications remain Phase 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .buybox import BuyBox
from .deal_input import Assumptions, build_deal_input, feed_row_id, should_feed
from .evaluator import Evaluation, evaluate
from .extraction import ExtractionLadder
from .models import Bucket, Email
from .rollup import roll_up
from .triage_log import build_triage_row


@dataclass
class EmailResult:
    email_id: str
    bucket: Bucket
    properties: list[tuple[dict, Evaluation]] = field(default_factory=list)
    used_ai: bool = False
    error: str | None = None
    subject: str = ""
    sender: str = ""
    deals_fed: int = 0


class Orchestrator:
    def __init__(
        self,
        gmail,
        sheets,
        ladder: ExtractionLadder,
        buybox: BuyBox,
        dry_run: bool = False,
        calculator=None,
        assumptions: Assumptions | None = None,
    ):
        self._gmail = gmail
        self._sheets = sheets
        self._ladder = ladder
        self._buybox = buybox
        self._dry_run = dry_run
        self._calculator = calculator
        self._assumptions = assumptions

    def run(self, cutoff: date, bucket_labels, limit: int | None = None) -> list[EmailResult]:
        metas = self._gmail.fetch_work_queue(cutoff, bucket_labels, limit)
        results: list[EmailResult] = []
        for meta in metas:
            email = self._gmail.fetch_email(meta.id)
            results.append(self.process_email(email))
        return results

    def _can_feed(self) -> bool:
        """Feeds require the standing assumptions (to build a valid Deal). Gated
        on assumptions, not the gateway, so a ``--dry-run`` still *previews* the
        feed count without a configured sink."""
        return self._assumptions is not None

    def process_email(self, email: Email) -> EmailResult:
        try:
            extraction = self._ladder.extract(email)
            properties = extraction.properties
            evaluations = [evaluate(fields, self._buybox) for fields in properties]
            bucket = roll_up([e.verdict for e in evaluations])

            # Decide the Calculator feed per Property (calc-ready + Pass/Needs-Human).
            # The Triage row carries the deterministic DEALS_APP id as the link, so
            # compute it up front — before the upsert — even though the actual feed
            # happens after (both are idempotent, so the order is crash-safe).
            feeds: list[tuple[str, dict]] = []
            rows = []
            for i, (fields, ev) in enumerate(zip(properties, evaluations)):
                row_id = ""
                if self._can_feed() and should_feed(ev):
                    row_id = feed_row_id(email.id, i)
                    feeds.append((row_id, build_deal_input(fields, self._assumptions)))
                rows.append(build_triage_row(email, i, fields, ev, deals_app_row_id=row_id))

            if not self._dry_run:
                if rows:
                    self._sheets.upsert_rows(rows)
                for row_id, deal_input in feeds:
                    self._calculator.upsert_deal(row_id, deal_input)
                # Label LAST — an Email is "done" only once it carries a Bucket.
                self._gmail.apply_label(email.id, bucket.value)

            return EmailResult(
                email.id,
                bucket,
                list(zip(properties, evaluations)),
                used_ai=extraction.used_ai,
                subject=email.subject,
                sender=email.from_addr,
                deals_fed=len(feeds),
            )
        except Exception as exc:  # handled mid-run failure -> Error (retryable)
            if not self._dry_run:
                self._gmail.apply_label(email.id, Bucket.ERROR.value)
            return EmailResult(
                email.id, Bucket.ERROR, error=str(exc), subject=email.subject, sender=email.from_addr
            )
