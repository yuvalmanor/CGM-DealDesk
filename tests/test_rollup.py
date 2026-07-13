"""Table tests for the Bucket Rollup precedence."""

from dealdesk.models import Bucket, Verdict
from dealdesk.rollup import roll_up


def test_no_property_is_not_a_deal():
    assert roll_up([]) is Bucket.NOT_A_DEAL


def test_one_pass_among_rejects_is_passed_buybox():
    assert roll_up([Verdict.REJECT, Verdict.PASS, Verdict.REJECT]) is Bucket.PASSED_BUYBOX


def test_no_pass_but_needs_human_is_needs_human():
    assert roll_up([Verdict.REJECT, Verdict.NEEDS_HUMAN]) is Bucket.NEEDS_HUMAN


def test_all_reject_is_rejected():
    assert roll_up([Verdict.REJECT, Verdict.REJECT]) is Bucket.REJECTED


def test_single_pass_is_passed_buybox():
    assert roll_up([Verdict.PASS]) is Bucket.PASSED_BUYBOX


def test_pass_outranks_needs_human():
    assert roll_up([Verdict.NEEDS_HUMAN, Verdict.PASS]) is Bucket.PASSED_BUYBOX
