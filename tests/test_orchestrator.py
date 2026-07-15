"""Orchestrator: happy path, rollup, Not-A-Deal, Error, and crash-recovery.

Uses in-memory fakes for the Gmail and Sheets gateways (the Sheets fake mirrors
the real gateway's idempotency contract: upsert keyed on (message-id, index)).
"""

import pytest

from dealdesk.buybox import BuyBox, FieldSpec
from dealdesk.deal_input import Assumptions, feed_row_id
from dealdesk.extraction import ExtractionResult
from dealdesk.models import Bucket, Email
from dealdesk.orchestrator import Orchestrator

BUYBOX = BuyBox(
    fields=(
        FieldSpec("purchase_price", "gate", "feed-required", "<=", 300000),
        FieldSpec("year_built", "gate", "none", ">=", 2000),
        FieldSpec("city", "gate", "none", "in", ["dallas"]),
        FieldSpec("monthly_rent", "none", "feed-required"),
    )
)

ASSUMPTIONS = Assumptions(hml_lev_pp=69.565, refi_ltv=65)


class _Crash(BaseException):
    """A hard crash — deliberately not an Exception, so the Orchestrator's
    `except Exception` (the Error-label path) does NOT catch it."""


class _FakeGmail:
    def __init__(self, emails, crash_on_label_once=False):
        self._emails = {e.id: e for e in emails}
        self._metas = list(emails)
        self.labels = {}
        self.sent = []  # (to, subject, body, sender) — Deal Notifications + Digest
        self._crash_pending = crash_on_label_once

    def fetch_work_queue(self, cutoff, bucket_labels, limit=None):
        return self._metas[:limit] if limit else self._metas

    def fetch_email(self, msg_id):
        return self._emails[msg_id]

    def apply_label(self, msg_id, label):
        if self._crash_pending:
            self._crash_pending = False
            raise _Crash("power lost before label")
        self.labels[msg_id] = label

    def send_message(self, to, subject, body, sender, label=None):
        self.sent.append((to, subject, body, sender, label))


class _FakeSheets:
    """Idempotent upsert keyed on row.key — the real gateway's contract. Also
    serves the `notified` guard read (`fetch_notified`) off the stored rows."""

    def __init__(self):
        self.rows = {}
        self.upsert_calls = 0

    def upsert_rows(self, rows):
        self.upsert_calls += 1
        for r in rows:
            self.rows[r.key] = r

    def fetch_notified(self, message_id):
        return {
            r.property_index
            for r in self.rows.values()
            if r.message_id == message_id and r.notified
        }


class _FakeCalculator:
    """Idempotent DEALS_APP feed keyed on row id — the real gateway's contract."""

    def __init__(self):
        self.deals = {}
        self.upsert_calls = 0

    def upsert_deal(self, row_id, deal_input):
        self.upsert_calls += 1
        self.deals[row_id] = deal_input
        return row_id


class _FakeLadder:
    def __init__(self, by_email, used_ai=False):
        self._by_email = by_email
        self._used_ai = used_ai

    def extract(self, email):
        result = self._by_email[email.id]
        if isinstance(result, Exception):
            raise result
        return ExtractionResult(result, used_ai=self._used_ai)


def _email(msg_id, from_addr="x@acme.com", subject=""):
    return Email(id=msg_id, from_addr=from_addr, subject=subject, date="d", body_text="body")


def test_result_carries_subject_and_sender():
    email = _email("e1", from_addr='"Acme" <blast@acme.com>', subject="123 Main St deal")
    gmail = _FakeGmail([email])
    orch = Orchestrator(gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), BUYBOX)

    result = orch.process_email(email)

    assert result.subject == "123 Main St deal"
    assert result.sender == '"Acme" <blast@acme.com>'


def _pass_fields():
    return {"purchase_price": 250000, "year_built": 2010, "city": "Dallas", "monthly_rent": 2000}


def _reject_fields():
    return {"purchase_price": 250000, "year_built": 1950, "city": "Dallas", "monthly_rent": 2000}


def test_happy_path_passes_writes_row_and_labels():
    gmail = _FakeGmail([_email("e1")])
    sheets = _FakeSheets()
    ladder = _FakeLadder({"e1": [_pass_fields()]})
    orch = Orchestrator(gmail, sheets, ladder, BUYBOX)

    results = orch.run(cutoff=None, bucket_labels=())

    assert results[0].bucket is Bucket.PASSED_BUYBOX
    assert len(sheets.rows) == 1
    assert gmail.labels["e1"] == "Passed-BuyBox"


def test_used_ai_flag_propagates_to_result():
    gmail = _FakeGmail([_email("e1")])
    ladder = _FakeLadder({"e1": [_pass_fields()]}, used_ai=True)
    orch = Orchestrator(gmail, _FakeSheets(), ladder, BUYBOX)

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.used_ai is True


def test_limit_caps_emails_processed():
    emails = [_email("e1"), _email("e2"), _email("e3")]
    gmail = _FakeGmail(emails)
    ladder = _FakeLadder({e.id: [_pass_fields()] for e in emails})
    orch = Orchestrator(gmail, _FakeSheets(), ladder, BUYBOX)

    results = orch.run(cutoff=None, bucket_labels=(), limit=2)

    assert [r.email_id for r in results] == ["e1", "e2"]


def test_multi_property_rollup_and_two_rows():
    gmail = _FakeGmail([_email("e1")])
    sheets = _FakeSheets()
    ladder = _FakeLadder({"e1": [_reject_fields(), _pass_fields()]})
    orch = Orchestrator(gmail, sheets, ladder, BUYBOX)

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.bucket is Bucket.PASSED_BUYBOX  # best verdict wins
    assert len(sheets.rows) == 2  # both Properties recorded


def test_no_property_is_not_a_deal_no_rows():
    gmail = _FakeGmail([_email("e1")])
    sheets = _FakeSheets()
    orch = Orchestrator(gmail, sheets, _FakeLadder({"e1": []}), BUYBOX)

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.bucket is Bucket.NOT_A_DEAL
    assert sheets.rows == {}
    assert gmail.labels["e1"] == "Not-A-Deal"


def test_handled_failure_lands_in_error():
    gmail = _FakeGmail([_email("e1")])
    ladder = _FakeLadder({"e1": RuntimeError("extraction blew up")})
    orch = Orchestrator(gmail, _FakeSheets(), ladder, BUYBOX)

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.bucket is Bucket.ERROR
    assert gmail.labels["e1"] == "Error"


def _needs_human_calc_ready_fields():
    # Missing a gate field (year_built) -> Needs-Human; price + rent present -> calc-ready.
    return {"purchase_price": 250000, "city": "Dallas", "monthly_rent": 2000}


def _pass_not_calc_ready_fields():
    # Passes gates, but missing feed-required rent -> not calc-ready.
    return {"purchase_price": 250000, "year_built": 2010, "city": "Dallas"}


def _feeding_orch(gmail, sheets, ladder, calculator):
    return Orchestrator(
        gmail, sheets, ladder, BUYBOX, calculator=calculator, assumptions=ASSUMPTIONS
    )


def test_calc_ready_pass_is_fed_and_triage_stores_row_id():
    gmail = _FakeGmail([_email("e1")])
    sheets = _FakeSheets()
    calc = _FakeCalculator()
    orch = _feeding_orch(gmail, sheets, _FakeLadder({"e1": [_pass_fields()]}), calc)

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.deals_fed == 1
    row_id = feed_row_id("e1", 0)
    assert row_id in calc.deals
    assert calc.deals[row_id]["purchasePrice"] == 250000
    # The Triage row links to the DEALS_APP row it fed.
    assert sheets.rows[("e1", "0")].deals_app_row_id == row_id


def test_calc_ready_needs_human_is_fed():
    gmail = _FakeGmail([_email("e1")])
    calc = _FakeCalculator()
    orch = _feeding_orch(gmail, _FakeSheets(), _FakeLadder({"e1": [_needs_human_calc_ready_fields()]}), calc)

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.bucket is Bucket.NEEDS_HUMAN
    assert result.deals_fed == 1
    assert feed_row_id("e1", 0) in calc.deals


def test_reject_is_never_fed():
    gmail = _FakeGmail([_email("e1")])
    calc = _FakeCalculator()
    orch = _feeding_orch(gmail, _FakeSheets(), _FakeLadder({"e1": [_reject_fields()]}), calc)

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.bucket is Bucket.REJECTED
    assert result.deals_fed == 0
    assert calc.deals == {}


def test_not_calc_ready_pass_is_not_fed():
    gmail = _FakeGmail([_email("e1")])
    sheets = _FakeSheets()
    calc = _FakeCalculator()
    orch = _feeding_orch(gmail, sheets, _FakeLadder({"e1": [_pass_not_calc_ready_fields()]}), calc)

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.deals_fed == 0
    assert calc.deals == {}
    assert sheets.rows[("e1", "0")].deals_app_row_id == ""  # no link stored


def test_multi_property_feeds_only_the_qualifying_one():
    gmail = _FakeGmail([_email("e1")])
    sheets = _FakeSheets()
    calc = _FakeCalculator()
    # index 0 Reject (not fed), index 1 Pass (fed).
    orch = _feeding_orch(gmail, sheets, _FakeLadder({"e1": [_reject_fields(), _pass_fields()]}), calc)

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.deals_fed == 1
    assert list(calc.deals) == [feed_row_id("e1", 1)]
    assert sheets.rows[("e1", "0")].deals_app_row_id == ""      # Reject: no link
    assert sheets.rows[("e1", "1")].deals_app_row_id == feed_row_id("e1", 1)


def test_rerun_does_not_duplicate_the_fed_deal():
    gmail = _FakeGmail([_email("e1")])
    calc = _FakeCalculator()
    orch = _feeding_orch(gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), calc)

    orch.process_email(gmail.fetch_email("e1"))
    orch.process_email(gmail.fetch_email("e1"))  # next daily run

    assert len(calc.deals) == 1        # idempotent by deterministic id
    assert calc.upsert_calls == 2      # it really did write again


def test_dry_run_previews_feed_without_writing():
    gmail = _FakeGmail([_email("e1")])
    calc = _FakeCalculator()
    orch = Orchestrator(
        gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), BUYBOX,
        dry_run=True, calculator=calc, assumptions=ASSUMPTIONS,
    )

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.deals_fed == 1   # previewed
    assert calc.deals == {}        # but nothing written
    assert "e1" not in gmail.labels


NOTIFY_TO = "operator@example.com"
NOTIFY_FROM = "deals@cgm-ventures.com"
NOTIFY_LABEL = "Deal Notifications"


def _notifying_orch(gmail, sheets, ladder, calculator=None):
    return Orchestrator(
        gmail, sheets, ladder, BUYBOX,
        calculator=calculator, assumptions=ASSUMPTIONS,
        notify_to=NOTIFY_TO, notify_from=NOTIFY_FROM, notify_label=NOTIFY_LABEL,
        calc_link="https://sheet/edit",
    )


def _deal_notifications(gmail):
    # The Daily Digest subject starts with "DealDesk daily digest"; everything
    # else is a per-deal notification.
    return [s for s in gmail.sent if not s[1].startswith("DealDesk daily digest")]


def test_passed_property_sends_one_notification_from_deals_mailbox():
    gmail = _FakeGmail([_email("e1", from_addr="blast@acme.com")])
    sheets = _FakeSheets()
    orch = _notifying_orch(gmail, sheets, _FakeLadder({"e1": [_pass_fields()]}), _FakeCalculator())

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.notified == 1
    deals = _deal_notifications(gmail)
    assert len(deals) == 1
    to, subject, body, sender, label = deals[0]
    assert to == NOTIFY_TO
    assert sender == NOTIFY_FROM          # sent from the deals mailbox
    assert label == NOTIFY_LABEL          # labeled directly, not via a filter
    assert "acme.com" in body             # Source
    assert "250,000" in body              # price
    assert "2,000" in body                # rent
    assert "Pass" in body                 # Verdict
    assert "https://sheet/edit" in body   # Calculator link
    # The Triage row is stamped notified so a retry won't re-send.
    assert sheets.rows[("e1", "0")].notified is True


def test_rerun_does_not_resend_notification():
    gmail = _FakeGmail([_email("e1")])
    sheets = _FakeSheets()
    orch = _notifying_orch(gmail, sheets, _FakeLadder({"e1": [_pass_fields()]}), _FakeCalculator())

    orch.process_email(gmail.fetch_email("e1"))
    orch.process_email(gmail.fetch_email("e1"))  # next daily run

    assert len(_deal_notifications(gmail)) == 1  # notified guard held


def test_needs_human_gets_no_per_deal_notification():
    gmail = _FakeGmail([_email("e1")])
    orch = _notifying_orch(
        gmail, _FakeSheets(), _FakeLadder({"e1": [_needs_human_calc_ready_fields()]}), _FakeCalculator()
    )

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.bucket is Bucket.NEEDS_HUMAN
    assert result.notified == 0
    assert _deal_notifications(gmail) == []


def test_reject_gets_no_notification():
    gmail = _FakeGmail([_email("e1")])
    orch = _notifying_orch(gmail, _FakeSheets(), _FakeLadder({"e1": [_reject_fields()]}))

    orch.process_email(gmail.fetch_email("e1"))

    assert _deal_notifications(gmail) == []


def test_run_sends_one_digest_covering_all_buckets():
    emails = [_email("e1"), _email("e2"), _email("e3")]
    gmail = _FakeGmail(emails)
    ladder = _FakeLadder({
        "e1": [_pass_fields()],
        "e2": [_needs_human_calc_ready_fields()],
        "e3": [_reject_fields()],
    })
    orch = _notifying_orch(gmail, _FakeSheets(), ladder, _FakeCalculator())

    orch.run(cutoff=None, bucket_labels=())

    digests = [s for s in gmail.sent if s[1].startswith("DealDesk daily digest")]
    assert len(digests) == 1  # exactly one per run
    body = digests[0][2]
    assert "Passed-BuyBox  1" in body
    assert "Needs-Human    1" in body
    assert "Rejected       1" in body


def test_dry_run_previews_notifications_without_sending():
    gmail = _FakeGmail([_email("e1")])
    orch = Orchestrator(
        gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), BUYBOX,
        dry_run=True, calculator=_FakeCalculator(), assumptions=ASSUMPTIONS,
        notify_to=NOTIFY_TO, notify_from=NOTIFY_FROM, calc_link="https://sheet/edit",
    )

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.notified == 1  # previewed
    assert gmail.sent == []       # but nothing sent


def test_no_recipient_configured_sends_nothing_and_does_not_mark_notified():
    gmail = _FakeGmail([_email("e1")])
    sheets = _FakeSheets()
    # No notify_to -> a live run must not mark the row notified (silent-drop guard).
    orch = Orchestrator(
        gmail, sheets, _FakeLadder({"e1": [_pass_fields()]}), BUYBOX,
        calculator=_FakeCalculator(), assumptions=ASSUMPTIONS,
    )

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.notified == 0
    assert gmail.sent == []
    assert sheets.rows[("e1", "0")].notified is False


def test_crash_before_label_reruns_without_duplicating_row():
    # Run 1: Triage row is written, then the process "crashes" before labeling.
    sheets = _FakeSheets()
    gmail = _FakeGmail([_email("e1")], crash_on_label_once=True)
    ladder = _FakeLadder({"e1": [_pass_fields()]})
    orch = Orchestrator(gmail, sheets, ladder, BUYBOX)

    with pytest.raises(_Crash):
        orch.process_email(gmail.fetch_email("e1"))

    assert len(sheets.rows) == 1       # row was written
    assert "e1" not in gmail.labels    # but the Email is NOT yet labeled ("done")

    # Run 2 (next daily run): re-process the still-unlabeled Email.
    orch.process_email(gmail.fetch_email("e1"))

    assert len(sheets.rows) == 1               # upsert updated, did not duplicate
    assert sheets.upsert_calls == 2            # it really did write again
    assert gmail.labels["e1"] == "Passed-BuyBox"  # now labeled last
