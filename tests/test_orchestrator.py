"""Orchestrator: happy path, rollup, Not-A-Deal, Error, and crash-recovery.

Uses in-memory fakes for the Gmail and Sheets gateways (the Sheets fake mirrors
the real gateway's idempotency contract: upsert keyed on (message-id, index)).
"""

import json
from datetime import date

import pytest

from dealdesk.buybox import BuyBox, FieldSpec
from dealdesk.deal_input import Assumptions, feed_row_id
from dealdesk.extraction import ExtractionResult
from dealdesk.models import Bucket, Email
from dealdesk.orchestrator import Orchestrator
from dealdesk.resend import PriorProperty

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
        self.removed = {}  # msg_id -> list of labels stripped in apply_label
        self.sent = []  # (to, subject, body, sender, label, cc) — Deal Notifications + Digest
        self._crash_pending = crash_on_label_once
        self.fetch_retryable = None  # retryable_labels seen by the last fetch
        self.fetch_self_addresses = None  # self_addresses seen by the last fetch

    def fetch_work_queue(
        self, cutoff, bucket_labels, limit=None, retryable_labels=(), self_addresses=()
    ):
        self.fetch_retryable = tuple(retryable_labels)
        self.fetch_self_addresses = tuple(self_addresses)
        return self._metas[:limit] if limit else self._metas

    def fetch_email(self, msg_id):
        return self._emails[msg_id]

    def apply_label(self, msg_id, label, remove_labels=()):
        if self._crash_pending:
            self._crash_pending = False
            raise _Crash("power lost before label")
        self.labels[msg_id] = label
        if remove_labels:
            self.removed[msg_id] = list(remove_labels)

    def send_message(self, to, subject, body, sender, label=None, cc=None):
        self.sent.append((to, subject, body, sender, label, cc))


class _FakeSheets:
    """Idempotent upsert keyed on row.key — the real gateway's contract. Also
    serves the `notified` guard read (`fetch_notified`) and the re-send lookup
    (`fetch_prior_properties`) off the stored rows."""

    def __init__(self):
        self.rows = {}
        self.upsert_calls = 0
        self.prior_reads = 0

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

    def fetch_prior_properties(self):
        self.prior_reads += 1
        return [
            PriorProperty(
                message_id=r.message_id,
                property_index=r.property_index,
                address=r.address,
                verdict=r.verdict,
                price=json.loads(r.facts_json).get("purchase_price"),
                received_date=r.received_date,
                deals_app_row_id=r.deals_app_row_id,
            )
            for r in self.rows.values()
        ]


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


def _email(msg_id, from_addr="x@acme.com", subject="", date_hdr="d"):
    return Email(id=msg_id, from_addr=from_addr, subject=subject, date=date_hdr, body_text="body")


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


# --- Phase 5: Error retry + age-based escalation ---------------------------

TODAY = date(2026, 7, 15)


def _erroring_orch(gmail, today=TODAY, escalate_after_days=3):
    ladder = _FakeLadder({"e1": RuntimeError("extraction blew up")})
    return Orchestrator(
        gmail, _FakeSheets(), ladder, BUYBOX,
        today=today, escalate_after_days=escalate_after_days,
    )


def test_run_folds_error_back_into_work_queue():
    # The run must not negate the retryable Error label, so a failed Email is
    # picked up again; terminal labels stay excluded.
    gmail = _FakeGmail([_email("e1")])
    orch = Orchestrator(gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), BUYBOX)

    orch.run(cutoff=None, bucket_labels=("Passed-BuyBox", "Rejected", "Error"))

    assert gmail.fetch_retryable == ("Error",)


def test_recent_error_email_stays_error_within_window():
    # Received 1 day ago — inside the escalation window — so it retries as Error.
    gmail = _FakeGmail([_email("e1", date_hdr="Tue, 14 Jul 2026 09:00:00 +0000")])
    orch = _erroring_orch(gmail)

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.bucket is Bucket.ERROR
    assert gmail.labels["e1"] == "Error"
    assert "e1" not in gmail.removed  # nothing to strip on a plain retry


def test_old_error_email_escalates_to_needs_human():
    # Received 5 days ago and still failing -> escalate to Needs-Human, stripping
    # the stale Error label so it ends in exactly one Bucket.
    gmail = _FakeGmail([_email("e1", date_hdr="Thu, 10 Jul 2026 09:00:00 +0000")])
    orch = _erroring_orch(gmail)

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.bucket is Bucket.NEEDS_HUMAN
    assert result.error  # the failure is still recorded
    assert gmail.labels["e1"] == "Needs-Human"
    assert gmail.removed["e1"] == ["Error"]


def test_escalation_boundary_is_the_configured_window():
    # Exactly at the window (3 days) escalates; the day before does not.
    at = _FakeGmail([_email("e1", date_hdr="Sun, 12 Jul 2026 09:00:00 +0000")])
    assert _erroring_orch(at).process_email(at.fetch_email("e1")).bucket is Bucket.NEEDS_HUMAN

    before = _FakeGmail([_email("e1", date_hdr="Mon, 13 Jul 2026 09:00:00 +0000")])
    assert _erroring_orch(before).process_email(before.fetch_email("e1")).bucket is Bucket.ERROR


def test_unparseable_date_cannot_be_aged_so_stays_error():
    # No usable received date -> can't escalate on age -> keep retrying as Error.
    gmail = _FakeGmail([_email("e1", date_hdr="not a date")])
    orch = _erroring_orch(gmail)

    assert orch.process_email(gmail.fetch_email("e1")).bucket is Bucket.ERROR


def test_transient_failure_that_later_succeeds_ends_in_terminal_bucket():
    email = _email("e1", date_hdr="Tue, 14 Jul 2026 09:00:00 +0000")
    gmail = _FakeGmail([email])

    # Run 1: extraction fails -> Error (within window).
    fail = Orchestrator(gmail, _FakeSheets(), _FakeLadder({"e1": RuntimeError("blip")}), BUYBOX, today=TODAY)
    assert fail.process_email(email).bucket is Bucket.ERROR
    assert gmail.labels["e1"] == "Error"

    # Run 2 (next daily run): the blip is gone; it passes and the stale Error
    # label is stripped so the Email ends in exactly one terminal Bucket.
    ok = Orchestrator(gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), BUYBOX, today=TODAY)
    result = ok.process_email(email)

    assert result.bucket is Bucket.PASSED_BUYBOX
    assert gmail.labels["e1"] == "Passed-BuyBox"
    assert gmail.removed["e1"] == ["Error"]


def test_escalation_is_suppressed_in_dry_run():
    gmail = _FakeGmail([_email("e1", date_hdr="Thu, 10 Jul 2026 09:00:00 +0000")])
    orch = Orchestrator(
        gmail, _FakeSheets(), _FakeLadder({"e1": RuntimeError("boom")}), BUYBOX,
        dry_run=True, today=TODAY,
    )

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.bucket is Bucket.NEEDS_HUMAN  # verdict computed
    assert gmail.labels == {}                    # but no label applied


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


def test_calc_ready_needs_human_is_not_fed_but_is_still_logged():
    # The handoff still happens everywhere except the Calculator: the Property
    # gets its Triage row and its Needs-Human bucket, it just isn't entered as a
    # Deal until a human supplies the missing gate fact.
    gmail = _FakeGmail([_email("e1")])
    sheets = _FakeSheets()
    calc = _FakeCalculator()
    orch = _feeding_orch(gmail, sheets, _FakeLadder({"e1": [_needs_human_calc_ready_fields()]}), calc)

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.bucket is Bucket.NEEDS_HUMAN
    assert result.deals_fed == 0
    assert calc.deals == {}
    # ...and the Triage row claims no DEALS_APP link, because none was written.
    assert sheets.rows[("e1", "0")].deals_app_row_id == ""


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


def test_feed_disabled_writes_nothing_and_stores_no_link():
    """The Calculator kill switch (`calculator.enabled = false`): a deal that
    would otherwise feed is triaged and logged, but never written to DEALS_APP —
    and the Triage row must not claim a row id that does not exist."""
    gmail = _FakeGmail([_email("e1")])
    sheets = _FakeSheets()
    calc = _FakeCalculator()
    orch = Orchestrator(
        gmail, sheets, _FakeLadder({"e1": [_pass_fields()]}), BUYBOX,
        calculator=calc, feed_enabled=False, assumptions=ASSUMPTIONS,
    )

    result = orch.process_email(gmail.fetch_email("e1"))

    assert result.bucket is Bucket.PASSED_BUYBOX   # triage is unaffected
    assert gmail.labels["e1"] == "Passed-BuyBox"   # labeling is unaffected
    assert result.deals_fed == 0
    assert calc.deals == {}
    assert calc.upsert_calls == 0                  # the gateway is never called
    assert sheets.rows[("e1", "0")].deals_app_row_id == ""


def test_feed_disabled_dry_run_previews_zero_feeds():
    """A dry-run must predict what a live run would do — with the feed off that
    is zero, not the count it would have fed."""
    gmail = _FakeGmail([_email("e1")])
    orch = Orchestrator(
        gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), BUYBOX,
        dry_run=True, calculator=_FakeCalculator(), feed_enabled=False,
        assumptions=ASSUMPTIONS,
    )

    assert orch.process_email(gmail.fetch_email("e1")).deals_fed == 0


def test_feed_disabled_notification_does_not_link_to_calculator():
    """The Deal Notification must not point at a sheet the deal was never
    written to — it says so, and tells the operator to enter it by hand."""
    gmail = _FakeGmail([_email("e1")])
    orch = Orchestrator(
        gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), BUYBOX,
        calculator=_FakeCalculator(), feed_enabled=False, assumptions=ASSUMPTIONS,
        notify_to=NOTIFY_TO, notify_from=NOTIFY_TO,
        calc_link="https://docs.google.com/spreadsheets/d/sheet-id/edit",
    )

    orch.process_email(gmail.fetch_email("e1"))

    body = gmail.sent[0][2]
    assert "feed disabled" in body.lower()
    assert "sheet-id" not in body   # the live sheet link is withheld


NOTIFY_TO = "operator@example.com"
NOTIFY_FROM = "deals@cgm-ventures.com"
NOTIFY_LABEL = "Deal Notifications"
NOTIFY_DEAL_CC = "extra@example.com"


def _notifying_orch(gmail, sheets, ladder, calculator=None, notify_deal_cc=""):
    return Orchestrator(
        gmail, sheets, ladder, BUYBOX,
        calculator=calculator, assumptions=ASSUMPTIONS,
        notify_to=NOTIFY_TO, notify_from=NOTIFY_FROM, notify_label=NOTIFY_LABEL,
        notify_deal_cc=notify_deal_cc,
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
    to, subject, body, sender, label, cc = deals[0]
    assert to == NOTIFY_TO
    assert sender == NOTIFY_FROM          # sent from the deals mailbox
    assert label == NOTIFY_LABEL          # labeled directly, not via a filter
    assert cc is None                     # no extra recipient unless configured
    assert "acme.com" in body             # Source
    assert "250,000" in body              # price
    assert "2,000" in body                # rent
    assert "Pass" in body                 # Verdict
    assert "https://sheet/edit" in body   # Calculator link
    # The Triage row is stamped notified so a retry won't re-send.
    assert sheets.rows[("e1", "0")].notified is True


def test_addressless_property_is_named_by_its_email_everywhere():
    # No address extracted: the row, the Calculator deal and the notification all
    # carry "<subject>|<sender>" instead of a blank the operator can't act on.
    gmail = _FakeGmail([_email("e1", from_addr="blast@acme.com", subject="Off market deal")])
    sheets = _FakeSheets()
    calc = _FakeCalculator()
    orch = _notifying_orch(gmail, sheets, _FakeLadder({"e1": [_pass_fields()]}), calc)

    orch.process_email(gmail.fetch_email("e1"))

    label = "Off market deal|blast@acme.com"
    assert sheets.rows[("e1", "0")].address == label
    assert calc.deals["dd-e1-0"]["address"] == label
    _, subject, body, *_ = _deal_notifications(gmail)[0]
    assert label in subject and label in body


def test_the_email_fallback_label_never_makes_two_deals_the_same_house():
    # Same blast subject and sender, two different Emails: the label identifies an
    # Email, not a property, so it must not stamp a re-send breadcrumb.
    emails = [
        _email("m1", from_addr="blast@acme.com", subject="New Deals This Week"),
        _email("m2", from_addr="blast@acme.com", subject="New Deals This Week"),
    ]
    gmail = _FakeGmail(emails)
    sheets = _FakeSheets()
    ladder = _FakeLadder({"m1": [_pass_fields()], "m2": [_pass_fields()]})
    orch = Orchestrator(gmail, sheets, ladder, BUYBOX)

    orch.run(cutoff=None, bucket_labels=())

    assert sheets.rows[("m1", "0")].address == "New Deals This Week|blast@acme.com"
    assert sheets.rows[("m2", "0")].resend_flag == ""


def test_passed_notification_ccs_the_configured_extra_recipient():
    # The new recipient rides along on the per-deal notification as a Cc, without
    # changing the send-to-self `to`.
    gmail = _FakeGmail([_email("e1", from_addr="blast@acme.com")])
    orch = _notifying_orch(
        gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), _FakeCalculator(),
        notify_deal_cc=NOTIFY_DEAL_CC,
    )

    orch.process_email(gmail.fetch_email("e1"))

    to, _subject, _body, _sender, _label, cc = _deal_notifications(gmail)[0]
    assert to == NOTIFY_TO          # send-to-self recipient unchanged
    assert cc == NOTIFY_DEAL_CC     # extra recipient added as a Cc


def test_digest_does_not_cc_the_extra_recipient():
    # deal_cc fans out per-deal Passed notifications only — the Daily Digest still
    # goes to the send-to-self recipient alone.
    emails = [_email("e1"), _email("e2")]
    gmail = _FakeGmail(emails)
    ladder = _FakeLadder({"e1": [_pass_fields()], "e2": [_reject_fields()]})
    orch = _notifying_orch(
        gmail, _FakeSheets(), ladder, _FakeCalculator(), notify_deal_cc=NOTIFY_DEAL_CC
    )

    orch.run(cutoff=None, bucket_labels=())

    digest = [s for s in gmail.sent if s[1].startswith("DealDesk daily digest")][0]
    assert digest[5] is None  # cc slot — digest carries no extra recipient


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


# --- Phase 6: re-send soft flag -------------------------------------------

MAIN_ST = "123 Main St, Dallas, TX"
MAIN_STREET = "123 Main Street, Dallas, TX"  # the same house, spelled differently


def _at(address, price=300000, year_built=2010):
    return {
        "address": address, "purchase_price": price,
        "year_built": year_built, "city": "Dallas", "monthly_rent": 2000,
    }


def _run_email(sheets, msg_id, fields, gmail=None):
    """One Email through its own Orchestrator — a fresh instance per call, so the
    re-send index is re-read from the sheet exactly as the next daily run would."""
    email = _email(msg_id)
    gmail = gmail or _FakeGmail([email])
    orch = Orchestrator(gmail, sheets, _FakeLadder({msg_id: [fields]}), BUYBOX)
    return orch.process_email(email)


def test_matching_address_stamps_a_resend_breadcrumb_on_the_new_row():
    sheets = _FakeSheets()
    # Last month: offered at $300k, too old -> Reject.
    _run_email(sheets, "m1", _at(MAIN_ST, price=300000, year_built=1950))
    # This month: same house, price dropped, and now it pencils.
    result = _run_email(sheets, "m2", _at(MAIN_STREET, price=250000))

    flag = sheets.rows[("m2", "0")].resend_flag
    assert flag.startswith("possible re-send")
    assert "Reject" in flag        # what the earlier row said
    assert "$300,000" in flag      # at what price — the price drop is the point
    assert result.resends == 1


def test_resend_is_recorded_as_a_new_row_and_the_prior_row_is_untouched():
    sheets = _FakeSheets()
    _run_email(sheets, "m1", _at(MAIN_ST, price=300000, year_built=1950))
    before = sheets.rows[("m1", "0")]

    _run_email(sheets, "m2", _at(MAIN_STREET, price=250000))

    # Two rows, distinct keys — never merged into one.
    assert set(sheets.rows) == {("m1", "0"), ("m2", "0")}
    # The prior row is byte-for-byte what it was: not re-evaluated, not restamped.
    assert sheets.rows[("m1", "0")] == before
    assert sheets.rows[("m1", "0")].verdict == "Reject"
    assert sheets.rows[("m1", "0")].resend_flag == ""


def test_a_rerun_does_not_flag_an_email_as_a_resend_of_itself():
    # m1's own row is in the Triage Log by the second run; it must not match itself.
    sheets = _FakeSheets()
    _run_email(sheets, "m1", _at(MAIN_ST))
    result = _run_email(sheets, "m1", _at(MAIN_ST))  # next daily run, same Email

    assert sheets.rows[("m1", "0")].resend_flag == ""
    assert result.resends == 0


def test_unrelated_address_gets_no_breadcrumb():
    sheets = _FakeSheets()
    _run_email(sheets, "m1", _at("999 Elm Rd, Dallas, TX"))
    result = _run_email(sheets, "m2", _at(MAIN_ST))

    assert sheets.rows[("m2", "0")].resend_flag == ""
    assert result.resends == 0


def test_resend_within_a_single_run_is_flagged():
    # Two wholesalers blast the same house the same day: the second Email is
    # flagged against the first without a second sheet read.
    emails = [_email("m1"), _email("m2")]
    gmail = _FakeGmail(emails)
    sheets = _FakeSheets()
    ladder = _FakeLadder({"m1": [_at(MAIN_ST)], "m2": [_at(MAIN_STREET)]})
    orch = Orchestrator(gmail, sheets, ladder, BUYBOX)

    orch.run(cutoff=None, bucket_labels=())

    assert sheets.rows[("m1", "0")].resend_flag == ""       # first sighting
    assert "possible re-send" in sheets.rows[("m2", "0")].resend_flag


def test_the_triage_log_is_scanned_once_per_run_not_once_per_email():
    emails = [_email("m1"), _email("m2"), _email("m3")]
    gmail = _FakeGmail(emails)
    sheets = _FakeSheets()
    ladder = _FakeLadder({e.id: [_at(MAIN_ST)] for e in emails})
    orch = Orchestrator(gmail, sheets, ladder, BUYBOX)

    orch.run(cutoff=None, bucket_labels=())

    assert sheets.prior_reads == 1  # the lookup stays cheap as the sheet grows


def test_addressless_properties_never_flag_each_other():
    # Two Properties whose address failed to extract are not "the same house".
    sheets = _FakeSheets()
    _run_email(sheets, "m1", _pass_fields())
    result = _run_email(sheets, "m2", _pass_fields())

    assert sheets.rows[("m2", "0")].resend_flag == ""
    assert result.resends == 0


def test_the_breadcrumb_does_not_pollute_the_extracted_facts():
    # facts_json records what the Source said; the flag is DealDesk's annotation.
    sheets = _FakeSheets()
    _run_email(sheets, "m1", _at(MAIN_ST, year_built=1950))
    _run_email(sheets, "m2", _at(MAIN_STREET))

    assert "resend" not in sheets.rows[("m2", "0")].facts_json


def test_the_breadcrumb_surfaces_in_the_deal_notification():
    sheets = _FakeSheets()
    _run_email(sheets, "m1", _at(MAIN_ST, price=300000, year_built=1950))

    # The price-drop re-send passes the Buy Box, so it earns a Deal Notification.
    email = _email("m2")
    gmail = _FakeGmail([email])
    orch = _notifying_orch(
        gmail, sheets, _FakeLadder({"m2": [_at(MAIN_STREET, price=250000)]}), _FakeCalculator()
    )
    orch.process_email(email)

    body = _deal_notifications(gmail)[0][2]
    assert "Re-send:" in body
    assert "$300,000" in body  # the operator sees the drop without opening anything


def test_a_resend_does_not_change_the_verdict():
    # The flag annotates; it never re-decides. The same fields evaluate the same
    # way whether or not the house was seen before.
    sheets = _FakeSheets()
    _run_email(sheets, "m1", _at(MAIN_ST, year_built=1950))
    result = _run_email(sheets, "m2", _at(MAIN_STREET, year_built=1950))

    assert result.bucket is Bucket.REJECTED
    assert sheets.rows[("m2", "0")].verdict == "Reject"


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


# ---- self-ingestion exclusion ---------------------------------------------


def test_work_queue_excludes_dealdesks_own_send_addresses():
    """DealDesk's notifications land in the mailbox it watches. The exclusion is
    handed to the queue, so its own output is never even fetched — the loop that
    wrote half the live Triage Log."""
    gmail = _FakeGmail([_email("e1")])
    orch = Orchestrator(
        gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), BUYBOX,
        self_addresses=("deals@cgm-ventures.com",),
    )

    orch.run(cutoff=None, bucket_labels=())

    assert gmail.fetch_self_addresses == ("deals@cgm-ventures.com",)


def test_work_queue_self_addresses_default_to_empty():
    gmail = _FakeGmail([_email("e1")])
    orch = Orchestrator(gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), BUYBOX)

    orch.run(cutoff=None, bucket_labels=())

    assert gmail.fetch_self_addresses == ()


# ---- same-offer guard (duplicate Calculator feeds) ------------------------


def _feed_run(sheets, calc, msg_id, fields):
    """One Email through a feeding Orchestrator, fresh per call — the re-send
    index is re-read from the sheet exactly as the next daily run would."""
    email = _email(msg_id)
    gmail = _FakeGmail([email])
    return _feeding_orch(gmail, sheets, _FakeLadder({msg_id: [fields]}), calc).process_email(email)


def test_same_house_same_price_in_a_later_email_is_not_fed_twice():
    """The 2002 Rockwall case: nine DEALS_APP rows for one $289,000 deal, because
    feed_row_id is per-Email and every re-send arrived in a new Email."""
    sheets, calc = _FakeSheets(), _FakeCalculator()
    _feed_run(sheets, calc, "m1", _at(MAIN_ST, price=250000))
    result = _feed_run(sheets, calc, "m2", _at(MAIN_STREET, price=250000))

    assert list(calc.deals) == [feed_row_id("m1", 0)]  # one row, not two
    assert result.deals_fed == 0
    assert result.feed_duplicates == 1


def test_the_deduped_property_still_gets_its_own_triage_row_and_breadcrumb():
    """Rows are never merged (US 27/28) — only the Calculator write is suppressed."""
    sheets, calc = _FakeSheets(), _FakeCalculator()
    _feed_run(sheets, calc, "m1", _at(MAIN_ST, price=250000))
    _feed_run(sheets, calc, "m2", _at(MAIN_STREET, price=250000))

    assert set(sheets.rows) == {("m1", "0"), ("m2", "0")}
    assert "possible re-send" in sheets.rows[("m2", "0")].resend_flag


def test_the_deduped_row_links_to_the_calculator_row_that_holds_the_deal():
    # Not a blank link: the operator must still be able to reach the deal.
    sheets, calc = _FakeSheets(), _FakeCalculator()
    _feed_run(sheets, calc, "m1", _at(MAIN_ST, price=250000))
    _feed_run(sheets, calc, "m2", _at(MAIN_STREET, price=250000))

    assert sheets.rows[("m2", "0")].deals_app_row_id == feed_row_id("m1", 0)


def test_a_price_drop_on_the_same_house_still_feeds():
    """The case the re-send flag exists for — a price cut reviving a deal must
    reach the Calculator as its own row."""
    sheets, calc = _FakeSheets(), _FakeCalculator()
    _feed_run(sheets, calc, "m1", _at(MAIN_ST, price=250000))
    result = _feed_run(sheets, calc, "m2", _at(MAIN_STREET, price=235000))

    assert set(calc.deals) == {feed_row_id("m1", 0), feed_row_id("m2", 0)}
    assert result.deals_fed == 1
    assert result.feed_duplicates == 0


def test_duplicate_within_a_single_run_is_suppressed():
    # Rockwall was fed three times inside one run, not only across days.
    emails = [_email("m1"), _email("m2"), _email("m3")]
    gmail = _FakeGmail(emails)
    sheets, calc = _FakeSheets(), _FakeCalculator()
    ladder = _FakeLadder({
        "m1": [_at(MAIN_ST, price=250000)],
        "m2": [_at(MAIN_STREET, price=250000)],
        "m3": [_at(MAIN_ST, price=250000)],
    })
    _feeding_orch(gmail, sheets, ladder, calc).run(cutoff=None, bucket_labels=())

    assert list(calc.deals) == [feed_row_id("m1", 0)]
    assert len(sheets.rows) == 3  # every sighting still logged


def test_a_rerun_of_the_same_email_still_updates_its_own_row():
    """The same-offer guard must not shadow feed_row_id's own idempotency: a
    re-run has to keep updating its row, not skip the write and go stale."""
    sheets, calc = _FakeSheets(), _FakeCalculator()
    _feed_run(sheets, calc, "m1", _at(MAIN_ST, price=250000))
    result = _feed_run(sheets, calc, "m1", _at(MAIN_ST, price=250000))

    assert list(calc.deals) == [feed_row_id("m1", 0)]
    assert result.deals_fed == 1        # re-written, not suppressed
    assert result.feed_duplicates == 0
    assert calc.upsert_calls == 2       # same id, updated in place


def test_addressless_duplicates_are_never_suppressed():
    """Two nameless deals at the same price are not provably the same house; a
    false merge is worse than a duplicate row (address.normalize_address)."""
    sheets, calc = _FakeSheets(), _FakeCalculator()
    fields = {"purchase_price": 250000, "year_built": 2010, "city": "Dallas", "monthly_rent": 2000}
    _feed_run(sheets, calc, "m1", dict(fields))
    result = _feed_run(sheets, calc, "m2", dict(fields))

    assert set(calc.deals) == {feed_row_id("m1", 0), feed_row_id("m2", 0)}
    assert result.feed_duplicates == 0


def test_an_unfed_prior_does_not_suppress_a_later_feed():
    """A Rejected sighting writes no DEALS_APP row, so a later qualifying offer
    at the same price must still feed."""
    sheets, calc = _FakeSheets(), _FakeCalculator()
    _feed_run(sheets, calc, "m1", _at(MAIN_ST, price=250000, year_built=1950))  # Reject
    result = _feed_run(sheets, calc, "m2", _at(MAIN_STREET, price=250000))

    assert sheets.rows[("m1", "0")].verdict == "Reject"
    assert list(calc.deals) == [feed_row_id("m2", 0)]
    assert result.deals_fed == 1


def test_fed_deal_carries_the_sender_as_seller_agent():
    """The sender is a header, so it never reaches the Extraction Ladder — the
    orchestrator has to hand it to the deal builder itself."""
    email = _email("e1", from_addr="Momentum Capital <dispo@dfwinvestments.com>")
    gmail = _FakeGmail([email])
    calc = _FakeCalculator()
    _feeding_orch(gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), calc).process_email(email)

    assert calc.deals[feed_row_id("e1", 0)]["sellerAgent"] == "Momentum Capital"


def test_fed_deal_never_inherits_the_calculators_example_seller():
    # No display name, no extracted seller: still an explicit key, never absent.
    email = _email("e1", from_addr="")
    gmail = _FakeGmail([email])
    calc = _FakeCalculator()
    _feeding_orch(gmail, _FakeSheets(), _FakeLadder({"e1": [_pass_fields()]}), calc).process_email(email)

    assert calc.deals[feed_row_id("e1", 0)]["sellerAgent"] == ""


def test_fed_deal_carries_sqft_end_to_end():
    email = _email("e1")
    gmail = _FakeGmail([email])
    calc = _FakeCalculator()
    fields = dict(_pass_fields(), sqft=2057)
    _feeding_orch(gmail, _FakeSheets(), _FakeLadder({"e1": [fields]}), calc).process_email(email)

    assert calc.deals[feed_row_id("e1", 0)]["sqft"] == 2057
