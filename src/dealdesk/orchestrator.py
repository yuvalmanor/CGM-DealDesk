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
triage->Calculator link.

Notifications (Phase 4) are the last, best-effort step — *after* the label, so
the Email is already "done" and a failed send never flips its Bucket to Error.
Each ``Passed-BuyBox`` Property (Verdict ``Pass``) gets one Deal Notification,
guarded by the ``notified`` flag on its Triage row: the orchestrator reads which
indices were already notified, marks the ones it sends, and so a retry never
re-emails. The per-run Daily Digest is assembled from all results and sent once
in ``run``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from email.utils import parsedate_to_datetime

from .buybox import BuyBox
from .deal_input import Assumptions, build_deal_input, feed_row_id, should_feed
from .evaluator import Evaluation, evaluate
from .extraction import ExtractionLadder
from .models import RETRYABLE_BUCKET_VALUES, Bucket, Email, Verdict
from .notifications import build_deal_notification, build_digest
from .rollup import roll_up
from .source import derive_source
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
    notified: int = 0


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
        notify_to: str = "",
        notify_from: str = "",
        notify_label: str = "",
        calc_link: str = "",
        today: date | None = None,
        escalate_after_days: int = 3,
    ):
        self._gmail = gmail
        self._sheets = sheets
        self._ladder = ladder
        self._buybox = buybox
        self._dry_run = dry_run
        self._calculator = calculator
        self._assumptions = assumptions
        self._notify_to = notify_to
        self._notify_from = notify_from
        self._notify_label = notify_label
        self._calc_link = calc_link
        self._today = today
        self._escalate_after_days = escalate_after_days

    def run(self, cutoff: date, bucket_labels, limit: int | None = None) -> list[EmailResult]:
        # Fold retryable Buckets (Error) back into the queue: they are not negated
        # in the query, so a failed Email is picked up and retried this run.
        retryable = tuple(l for l in bucket_labels if l in RETRYABLE_BUCKET_VALUES)
        metas = self._gmail.fetch_work_queue(cutoff, bucket_labels, limit, retryable_labels=retryable)
        results: list[EmailResult] = []
        for meta in metas:
            email = self._gmail.fetch_email(meta.id)
            results.append(self.process_email(email))
        self._send_digest(results)
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

            # A Passed-BuyBox Property (Verdict Pass) earns a Deal Notification —
            # but only if not already notified on a prior run (the `notified`
            # guard). Read the prior state so the retry never re-emails.
            already = self._already_notified(email.id)
            candidates = [
                i for i, ev in enumerate(evaluations)
                if ev.verdict is Verdict.PASS and i not in already
            ]
            # We only send (and only mark notified) when a live run has a
            # recipient configured — otherwise a marked-but-never-sent row would
            # be a silent drop. Marking happens in the same upsert as the row.
            will_send = (not self._dry_run) and bool(self._notify_to)
            send_now = candidates if will_send else []
            notify_set = set(send_now)

            # Decide the Calculator feed per Property (calc-ready + Pass/Needs-Human).
            # The Triage row carries the deterministic DEALS_APP id as the link, so
            # compute it up front — before the upsert — even though the actual feed
            # happens after (both are idempotent, so the order is crash-safe).
            feeds: list[tuple[str, dict]] = []
            fed_ids: dict[int, str] = {}
            rows = []
            for i, (fields, ev) in enumerate(zip(properties, evaluations)):
                row_id = ""
                if self._can_feed() and should_feed(ev):
                    row_id = feed_row_id(email.id, i)
                    feeds.append((row_id, build_deal_input(fields, self._assumptions)))
                    fed_ids[i] = row_id
                notified = (i in already) or (i in notify_set)
                rows.append(
                    build_triage_row(email, i, fields, ev, deals_app_row_id=row_id, notified=notified)
                )

            if not self._dry_run:
                if rows:
                    self._sheets.upsert_rows(rows)
                for row_id, deal_input in feeds:
                    self._calculator.upsert_deal(row_id, deal_input)
                # Label LAST — an Email is "done" only once it carries a Bucket.
                # Strip any stale ``Error`` label: a retry that now succeeds must
                # end carrying exactly one (terminal) Bucket.
                self._gmail.apply_label(
                    email.id, bucket.value, remove_labels=(Bucket.ERROR.value,)
                )
                # Notifications are best-effort *after* the Email is done.
                self._send_deal_notifications(email, properties, evaluations, send_now, fed_ids)

            # dry-run previews would-notify; a live run reports what it queued.
            notified_count = len(candidates) if self._dry_run else len(send_now)
            return EmailResult(
                email.id,
                bucket,
                list(zip(properties, evaluations)),
                used_ai=extraction.used_ai,
                subject=email.subject,
                sender=email.from_addr,
                deals_fed=len(feeds),
                notified=notified_count,
            )
        except Exception as exc:  # handled mid-run failure -> Error (retryable)
            bucket = self._error_bucket(email)
            if not self._dry_run:
                # On escalation the Email leaves Error for Needs-Human; strip the
                # stale Error label so it ends in exactly one Bucket. A plain retry
                # (still Error) just re-adds Error, so nothing to remove.
                remove = (Bucket.ERROR.value,) if bucket is not Bucket.ERROR else ()
                self._gmail.apply_label(email.id, bucket.value, remove_labels=remove)
            return EmailResult(
                email.id, bucket, error=str(exc), subject=email.subject, sender=email.from_addr
            )

    def _error_bucket(self, email: Email) -> Bucket:
        """Where a failed Email lands. Normally ``Error`` (retryable, retried every
        run). But an Email that has been failing long enough escalates to
        ``Needs-Human`` so a genuinely broken item reaches the operator instead of
        retrying forever. Age stands in for a retry counter — no ledger (US 11):
        an Email older than the escalation window that fails is escalated. An
        unparseable/missing received date can't be aged, so it stays ``Error``."""
        age = self._email_age_days(email)
        if age is not None and age >= self._escalate_after_days:
            return Bucket.NEEDS_HUMAN
        return Bucket.ERROR

    def _email_age_days(self, email: Email) -> int | None:
        if not email.date:
            return None
        try:
            received = parsedate_to_datetime(email.date)
        except (TypeError, ValueError):  # malformed Date header — can't be aged
            return None
        if received is None:
            return None
        today = self._today or date.today()
        return (today - received.date()).days

    def _already_notified(self, message_id: str) -> set[int]:
        """Property indices already notified on a prior run (empty in dry-run or
        when the sheet can't be read). The `notified` guard's read side."""
        if self._dry_run or self._sheets is None:
            return set()
        fetch = getattr(self._sheets, "fetch_notified", None)
        if fetch is None:
            return set()
        return fetch(message_id)

    def _send_deal_notifications(self, email, properties, evaluations, send_now, fed_ids) -> None:
        source = derive_source(email.from_addr)
        for i in send_now:
            fields, ev = properties[i], evaluations[i]
            notification = build_deal_notification(
                fields,
                ev,
                source=source,
                calc_link=self._calc_link_for(fed_ids.get(i)),
                resend_flag=str(fields.get("resend_flag", "")),
            )
            self._gmail.send_message(
                self._notify_to, notification.subject, notification.body,
                self._notify_from, label=self._notify_label or None,
            )

    def _send_digest(self, results: list[EmailResult]) -> None:
        """One Daily Digest per run, sent last. Skipped in dry-run or when no
        recipient is configured."""
        if self._dry_run or not self._notify_to or not results:
            return
        digest = build_digest(results)
        self._gmail.send_message(
            self._notify_to, digest.subject, digest.body,
            self._notify_from, label=self._notify_label or None,
        )

    def _calc_link_for(self, row_id: str | None) -> str:
        """Link to the Calculator for a fed Property — the sheet plus its row id.
        A Pass that wasn't calc-ready has no fed row; the bare sheet link stands."""
        base = self._calc_link or "(Calculator not configured)"
        return f"{base} (row {row_id})" if row_id else base
