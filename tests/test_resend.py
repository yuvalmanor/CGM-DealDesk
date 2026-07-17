"""Re-send lookup + breadcrumb goldens.

Covers the two halves through their public interfaces: ``ResendIndex`` (which
prior Properties match an address) and ``build_resend_flag`` (what the breadcrumb
says). The self-exclusion cases are the load-bearing ones — they are what keeps a
re-run from flagging an Email as a re-send of itself.
"""

from dealdesk.resend import PriorProperty, ResendIndex, build_resend_flag

JUNE = "Fri, 12 Jun 2026 09:00:00 +0000"


def _prior(msg_id="m1", index=0, address="123 Main St", verdict="Reject",
           price=300000.0, received=JUNE):
    return PriorProperty(
        message_id=msg_id, property_index=index, address=address,
        verdict=verdict, price=price, received_date=received,
    )


# --- ResendIndex: what matches --------------------------------------------

def test_matching_address_finds_the_prior_property():
    index = ResendIndex([_prior()])

    assert index.lookup("123 Main Street", exclude_message_id="m2") == [_prior()]


def test_different_address_finds_nothing():
    index = ResendIndex([_prior(address="123 Main St")])

    assert index.lookup("999 Elm Rd", exclude_message_id="m2") == []


def test_lookup_excludes_the_email_being_processed():
    # The re-run case: m1's own row is already in the Triage Log, and without
    # this exclusion m1 would be flagged as a re-send of itself.
    index = ResendIndex([_prior(msg_id="m1")])

    assert index.lookup("123 Main St", exclude_message_id="m1") == []
    assert index.lookup("123 Main St", exclude_message_id="m2") != []


def test_one_email_listing_the_same_house_twice_does_not_self_flag():
    index = ResendIndex([_prior(msg_id="m1", index=0), _prior(msg_id="m1", index=1)])

    assert index.lookup("123 Main St", exclude_message_id="m1") == []


def test_addressless_prior_is_never_matchable():
    index = ResendIndex([_prior(address="")])

    assert index.lookup("", exclude_message_id="m2") == []


def test_addressless_lookup_matches_nothing():
    index = ResendIndex([_prior(address="123 Main St")])

    assert index.lookup(None, exclude_message_id="m2") == []


def test_readding_the_same_property_replaces_rather_than_duplicates():
    # A re-run re-records m1/0; the index must mirror the Triage Log's key, not
    # stack a second copy that would inflate the "N earlier rows" count.
    index = ResendIndex([_prior(msg_id="m1", verdict="Reject")])
    index.add(_prior(msg_id="m1", verdict="Pass"))

    found = index.lookup("123 Main St", exclude_message_id="m2")
    assert len(found) == 1
    assert found[0].verdict == "Pass"


def test_index_grows_during_a_run():
    # A house recorded earlier in this run is a prior for a later Email.
    index = ResendIndex()
    assert index.lookup("123 Main St", exclude_message_id="m2") == []

    index.add(_prior(msg_id="m1"))

    assert index.lookup("123 Main St", exclude_message_id="m2") == [_prior(msg_id="m1")]


def test_priors_are_returned_in_sheet_order():
    first, second = _prior(msg_id="m1"), _prior(msg_id="m2")
    index = ResendIndex([first, second])

    assert index.lookup("123 Main St", exclude_message_id="m3") == [first, second]


# --- build_resend_flag: what the breadcrumb says --------------------------

def test_no_priors_means_no_flag():
    assert build_resend_flag([]) == ""


def test_flag_reports_the_earlier_verdict_price_and_date():
    flag = build_resend_flag([_prior(verdict="Rejected", price=300000.0)])

    assert flag == "possible re-send — earlier row was Rejected @ $300,000 on 2026-06-12"


def test_flag_is_hedged_never_asserted():
    # The address key is evidence, not proof — the operator decides.
    assert build_resend_flag([_prior()]).startswith("possible re-send")


def test_flag_uses_the_most_recent_prior_and_counts_the_rest():
    older = _prior(msg_id="m1", verdict="Rejected", price=320000.0)
    newer = _prior(msg_id="m2", verdict="Rejected", price=300000.0)

    flag = build_resend_flag([older, newer])

    assert "$300,000" in flag       # the latest row's price
    assert "$320,000" not in flag
    assert "(2 earlier rows)" in flag


def test_flag_omits_price_when_the_earlier_row_had_none():
    flag = build_resend_flag([_prior(price=None)])

    assert "@" not in flag
    assert "Reject" in flag


def test_flag_omits_date_when_it_cannot_be_parsed():
    flag = build_resend_flag([_prior(received="not a date")])

    assert " on " not in flag
    assert "$300,000" in flag


def test_flag_survives_a_row_with_no_recorded_verdict():
    assert "unrecorded" in build_resend_flag([_prior(verdict="")])
