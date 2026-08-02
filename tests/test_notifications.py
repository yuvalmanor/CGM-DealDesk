"""Golden/content tests for the Notification Builder (pure).

Asserts external behavior through the public interface: given a Property (fields +
Evaluation) or a set of run results, the produced email content carries the
required facts. No Gmail, no I/O.
"""

from dealdesk.evaluator import Evaluation
from dealdesk.models import Bucket, Verdict
from dealdesk.notifications import build_deal_notification, build_digest
from dealdesk.orchestrator import EmailResult


def _pass_eval():
    return Evaluation(Verdict.PASS, ("all Buy Box gates pass",), calc_ready=True, missing_fields=())


def _needs_human_eval(missing=("year_built",)):
    reasons = tuple(f"missing {m}" for m in missing)
    return Evaluation(Verdict.NEEDS_HUMAN, reasons, calc_ready=True, missing_fields=tuple(missing))


def _reject_eval():
    return Evaluation(Verdict.REJECT, ("year_built 1950 fails >= 1995",), calc_ready=True, missing_fields=())


# --- Deal Notification ------------------------------------------------------

def test_deal_notification_carries_every_required_fact():
    fields = {
        "address": "123 Main St, Dallas TX",
        "purchase_price": 250000,
        "monthly_rent": 2000,
        "arv": 320000,
    }
    n = build_deal_notification(
        fields, _pass_eval(), source="acme.com", calc_link="https://sheet/edit (row dd-e1-0)"
    )

    assert "123 Main St, Dallas TX" in n.subject
    assert "acme.com" in n.subject
    body = n.body
    assert "123 Main St, Dallas TX" in body   # address
    assert "acme.com" in body                  # Source
    assert "$250,000" in body                  # price
    assert "$2,000" in body                    # rent
    assert "$320,000" in body                  # ARV
    assert "Pass" in body                      # Verdict
    assert "all Buy Box gates pass" in body    # why
    assert "https://sheet/edit (row dd-e1-0)" in body  # Calculator link


def test_deal_notification_missing_arv_reads_fill_in():
    fields = {"address": "1 Oak", "purchase_price": 200000, "monthly_rent": 1500}
    n = build_deal_notification(fields, _pass_eval(), source="acme.com", calc_link="link")
    assert "fill in" in n.body            # ARV unknown, not "$0"
    assert "$0" not in n.body


def test_deal_notification_includes_resend_flag_when_present():
    fields = {"address": "1 Oak", "purchase_price": 200000, "monthly_rent": 1500}
    n = build_deal_notification(
        fields, _pass_eval(), source="acme.com", calc_link="link",
        resend_flag="possible re-send — earlier row was Rejected @ $300k",
    )
    assert "Re-send:" in n.body
    assert "earlier row was Rejected @ $300k" in n.body


def test_deal_notification_omits_resend_line_when_absent():
    fields = {"address": "1 Oak", "purchase_price": 200000, "monthly_rent": 1500}
    n = build_deal_notification(fields, _pass_eval(), source="acme.com", calc_link="link")
    assert "Re-send:" not in n.body


def test_deal_notification_without_an_address_names_the_email():
    fields = {"purchase_price": 200000, "monthly_rent": 1500}
    n = build_deal_notification(
        fields, _pass_eval(), source="acme.com", calc_link="link",
        email_subject="Off market deal", email_sender="deals@acme.com",
    )
    assert "Off market deal|deals@acme.com" in n.subject
    assert "Off market deal|deals@acme.com" in n.body
    assert "address unknown" not in n.body


# --- Daily Digest -----------------------------------------------------------

def _result(bucket, properties=(), subject="", sender="x@acme.com", error=None):
    return EmailResult(
        email_id="e", bucket=bucket, properties=list(properties),
        subject=subject, sender=sender, error=error,
    )


def test_digest_counts_and_lists_all_buckets():
    results = [
        _result(Bucket.PASSED_BUYBOX, [({"address": "1 Oak"}, _pass_eval())], sender="a@acme.com"),
        _result(Bucket.REJECTED, [({"address": "2 Elm"}, _reject_eval())], sender="b@wholesale.com"),
        _result(Bucket.NOT_A_DEAL, subject="Newsletter", sender="news@mls.com"),
        _result(Bucket.ERROR, subject="Weird email", sender="c@x.com", error="boom"),
    ]

    digest = build_digest(results)

    assert digest.subject.startswith("DealDesk daily digest")
    body = digest.body
    assert "Passed-BuyBox  1" in body
    assert "Rejected       1" in body
    assert "Not-A-Deal     1" in body
    assert "Error          1" in body
    # Listings across all buckets.
    assert "1 Oak" in body
    assert "2 Elm" in body
    assert "Newsletter" in body
    assert "Weird email" in body
    assert "boom" in body                 # Error carries the failure text


def test_digest_needs_human_shows_what_is_missing():
    results = [
        _result(
            Bucket.NEEDS_HUMAN,
            [({"address": "9 Pine"}, _needs_human_eval(("year_built", "property_type")))],
            sender="a@acme.com",
        ),
    ]

    body = build_digest(results).body

    assert "9 Pine" in body
    assert "missing year_built, property_type" in body


def test_digest_line_without_an_address_names_the_email():
    results = [
        _result(
            Bucket.NEEDS_HUMAN,
            [({"purchase_price": 250000}, _needs_human_eval(("address",)))],
            subject="Weekly list", sender="a@acme.com",
        ),
    ]

    body = build_digest(results).body

    assert "Weekly list|a@acme.com" in body
    assert "address unknown" not in body


def test_empty_run_still_produces_a_digest():
    digest = build_digest([])
    assert digest.subject.startswith("DealDesk daily digest")
    assert "Passed-BuyBox  0" in digest.body
