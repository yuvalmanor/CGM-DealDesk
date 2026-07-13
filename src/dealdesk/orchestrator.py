"""Orchestrator — the per-Email triage spine.

For each Email, enforce the ordering that guarantees the two headline invariants:

    extract -> evaluate each Property -> rollup -> upsert Triage Log -> label LAST

Because the Triage Log upsert is idempotent (keyed on message-id + index) and the
Bucket label is applied *last*, a hard crash after the upsert but before the
label leaves the Email unlabeled — it's re-processed next run and the upsert
updates rather than duplicates. A *handled* mid-run failure lands the Email in
``Error`` (retryable).

Phase 2 scope: no Calculator feed, no notifications. Those are Phases 3 and 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .buybox import BuyBox
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
    error: str | None = None


class Orchestrator:
    def __init__(self, gmail, sheets, ladder: ExtractionLadder, buybox: BuyBox, dry_run: bool = False):
        self._gmail = gmail
        self._sheets = sheets
        self._ladder = ladder
        self._buybox = buybox
        self._dry_run = dry_run

    def run(self, cutoff: date, bucket_labels) -> list[EmailResult]:
        metas = self._gmail.fetch_work_queue(cutoff, bucket_labels)
        results: list[EmailResult] = []
        for meta in metas:
            email = self._gmail.fetch_email(meta.id)
            results.append(self.process_email(email))
        return results

    def process_email(self, email: Email) -> EmailResult:
        try:
            properties = self._ladder.extract(email)
            evaluations = [evaluate(fields, self._buybox) for fields in properties]
            bucket = roll_up([e.verdict for e in evaluations])

            rows = [
                build_triage_row(email, i, fields, ev)
                for i, (fields, ev) in enumerate(zip(properties, evaluations))
            ]
            if not self._dry_run:
                if rows:
                    self._sheets.upsert_rows(rows)
                # Label LAST — an Email is "done" only once it carries a Bucket.
                self._gmail.apply_label(email.id, bucket.value)

            return EmailResult(email.id, bucket, list(zip(properties, evaluations)))
        except Exception as exc:  # handled mid-run failure -> Error (retryable)
            if not self._dry_run:
                self._gmail.apply_label(email.id, Bucket.ERROR.value)
            return EmailResult(email.id, Bucket.ERROR, error=str(exc))
