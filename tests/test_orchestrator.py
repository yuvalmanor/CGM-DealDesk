"""Orchestrator: happy path, rollup, Not-A-Deal, Error, and crash-recovery.

Uses in-memory fakes for the Gmail and Sheets gateways (the Sheets fake mirrors
the real gateway's idempotency contract: upsert keyed on (message-id, index)).
"""

import pytest

from dealdesk.buybox import BuyBox, FieldSpec
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


class _Crash(BaseException):
    """A hard crash — deliberately not an Exception, so the Orchestrator's
    `except Exception` (the Error-label path) does NOT catch it."""


class _FakeGmail:
    def __init__(self, emails, crash_on_label_once=False):
        self._emails = {e.id: e for e in emails}
        self._metas = list(emails)
        self.labels = {}
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


class _FakeSheets:
    """Idempotent upsert keyed on row.key — the real gateway's contract."""

    def __init__(self):
        self.rows = {}
        self.upsert_calls = 0

    def upsert_rows(self, rows):
        self.upsert_calls += 1
        for r in rows:
            self.rows[r.key] = r


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
