"""Triage Log row building + serialization."""

import json

from dealdesk.evaluator import Evaluation
from dealdesk.models import Email, Verdict
from dealdesk.triage_log import HEADER, build_triage_row


def _email():
    return Email(
        id="msg-1",
        from_addr='"Acme" <deals@acme.com>',
        subject="deal",
        date="Mon, 01 Jul 2026 10:00:00 -0500",
        body_text="",
    )


def test_row_carries_facts_verdict_reasons_and_calc_ready():
    fields = {"address": "9 Oak Dr", "purchase_price": 250000}
    ev = Evaluation(Verdict.PASS, ("all Buy Box gates pass",), True, ())
    row = build_triage_row(_email(), 0, fields, ev)

    assert row.key == ("msg-1", "0")
    assert row.source == "acme.com"
    assert row.address == "9 Oak Dr"
    assert json.loads(row.facts_json)["purchase_price"] == 250000
    assert row.verdict == "Pass"
    assert row.reasons == "all Buy Box gates pass"

    values = row.to_values()
    assert len(values) == len(HEADER)
    assert values[HEADER.index("calc_ready")] == "TRUE"
    assert values[HEADER.index("notified")] == "FALSE"


def test_row_without_an_address_names_the_email_instead():
    # A blank address column can't be read or searched; the Email's subject and
    # sender are what the operator would use to pull it up in Gmail.
    ev = Evaluation(Verdict.NEEDS_HUMAN, ("missing address",), False, ("address",))
    row = build_triage_row(_email(), 0, {"purchase_price": 250000}, ev)
    assert row.address == 'deal|"Acme" <deals@acme.com>'
    # The fallback is DealDesk's annotation, not a fact the Source stated.
    assert "address" not in json.loads(row.facts_json)


def test_needs_human_row_records_missing_fields():
    ev = Evaluation(Verdict.NEEDS_HUMAN, ("missing city",), False, ("city",))
    row = build_triage_row(_email(), 1, {"purchase_price": 250000}, ev)
    assert row.missing_fields == "city"
    assert row.to_values()[HEADER.index("calc_ready")] == "FALSE"
