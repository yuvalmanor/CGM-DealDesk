"""Deal Input Builder — golden/table tests for the ADR-0002 contract.

Exercises the public interface: given extracted Property fields + the standing
assumptions, assert the resulting partial ``Deal`` dict — marker-key presence,
the ``arv: 0`` sentinel, real (never-0) assumption markers, and the
DealDesk->Calculator field mapping. Plus the feed routing rule and the
deterministic row id.
"""

import pytest

from dealdesk.deal_input import (
    MARKER_KEYS,
    Assumptions,
    already_fed,
    build_deal_input,
    feed_row_id,
    should_feed,
)
from dealdesk.evaluator import Evaluation
from dealdesk.models import Verdict
from dealdesk.resend import PriorProperty

ASSUMPTIONS = Assumptions(hml_lev_pp=69.565, refi_ltv=65)


def _full_fields():
    return {
        "address": "9 Oak Dr, Dallas TX",
        "purchase_price": 250000,
        "monthly_rent": 2000,
        "arv": 320000,
        "year_built": 2010,
    }


# ---- marker keys ----------------------------------------------------------


def test_all_five_marker_keys_always_present():
    deal = build_deal_input(_full_fields(), ASSUMPTIONS)
    for key in MARKER_KEYS:
        assert key in deal


def test_marker_keys_present_even_when_arv_and_others_missing():
    deal = build_deal_input({"purchase_price": 250000, "monthly_rent": 2000}, ASSUMPTIONS)
    for key in MARKER_KEYS:
        assert key in deal


# ---- arv: 0 sentinel ------------------------------------------------------


def test_missing_arv_is_written_as_zero_sentinel():
    deal = build_deal_input({"purchase_price": 250000, "monthly_rent": 2000}, ASSUMPTIONS)
    assert deal["arv"] == 0


def test_unparseable_arv_is_written_as_zero_sentinel():
    fields = {"purchase_price": 250000, "monthly_rent": 2000, "arv": "call for ARV"}
    assert build_deal_input(fields, ASSUMPTIONS)["arv"] == 0


def test_present_arv_is_carried_through():
    assert build_deal_input(_full_fields(), ASSUMPTIONS)["arv"] == 320000


def test_missing_rent_is_written_as_zero_sentinel_never_null():
    # Rent is feed-optional now: a calc-ready deal can lack it. monthlyRent is a
    # marker key, so it must carry a value (0), never null.
    deal = build_deal_input({"purchase_price": 250000}, ASSUMPTIONS)
    assert deal["monthlyRent"] == 0
    assert deal["monthlyRent"] is not None


# ---- assumption markers carry real values (never 0) -----------------------


def test_assumption_markers_carry_real_config_values():
    deal = build_deal_input(_full_fields(), ASSUMPTIONS)
    assert deal["hmlLevPP"] == 69.565
    assert deal["refiLtv"] == 65
    assert deal["hmlLevPP"] != 0 and deal["refiLtv"] != 0


def test_zero_assumption_marker_is_refused():
    # A 0 assumption would pass the Calculator's key-presence guard but silently
    # zero out leverage — the builder must refuse it.
    with pytest.raises(ValueError):
        build_deal_input(_full_fields(), Assumptions(hml_lev_pp=0, refi_ltv=65))
    with pytest.raises(ValueError):
        build_deal_input(_full_fields(), Assumptions(hml_lev_pp=69.565, refi_ltv=0))


# ---- field mapping --------------------------------------------------------


def test_dealdesk_fields_map_to_calculator_keys():
    deal = build_deal_input(_full_fields(), ASSUMPTIONS)
    assert deal["purchasePrice"] == 250000
    assert deal["monthlyRent"] == 2000
    assert deal["yearBuilt"] == 2010
    assert deal["address"] == "9 Oak Dr, Dallas TX"


def test_currency_formatted_values_are_parsed_to_numbers():
    fields = {"purchase_price": "$250,000", "monthly_rent": "2,000", "arv": "$320,000.50"}
    deal = build_deal_input(fields, ASSUMPTIONS)
    assert deal["purchasePrice"] == 250000
    assert deal["monthlyRent"] == 2000
    assert deal["arv"] == 320000.50


def test_optional_fields_omitted_when_absent():
    deal = build_deal_input({"purchase_price": 250000, "monthly_rent": 2000}, ASSUMPTIONS)
    assert "yearBuilt" not in deal
    assert "address" not in deal


def test_address_label_names_a_deal_that_has_no_extracted_address():
    # The caller's label (subject|sender when no address was found) keeps a fed
    # deal from arriving nameless in the Calculator.
    deal = build_deal_input(
        {"purchase_price": 250000, "monthly_rent": 2000},
        ASSUMPTIONS,
        address="Off market deal|deals@acme.com",
    )
    assert deal["address"] == "Off market deal|deals@acme.com"


def test_extracted_address_is_used_when_no_label_is_given():
    deal = build_deal_input(_full_fields(), ASSUMPTIONS, address="")
    assert deal["address"] == "9 Oak Dr, Dallas TX"


# ---- feed routing rule ----------------------------------------------------


def _eval(verdict, calc_ready):
    return Evaluation(verdict, (), calc_ready, ())


def test_calc_ready_pass_is_fed():
    assert should_feed(_eval(Verdict.PASS, True)) is True


def test_calc_ready_needs_human_is_not_fed():
    # Only a clean pass feeds (narrowed 2026-08-02). Calc-ready says the
    # Calculator's inputs are present, not that any gate was cleared — a Property
    # missing `year_built` is calc-ready and Needs-Human at the same time, and
    # used to reach DEALS_APP having never been checked against the year gate.
    assert should_feed(_eval(Verdict.NEEDS_HUMAN, True)) is False


def test_reject_is_never_fed_even_when_calc_ready():
    assert should_feed(_eval(Verdict.REJECT, True)) is False


def test_not_calc_ready_is_not_fed():
    assert should_feed(_eval(Verdict.PASS, False)) is False


# ---- deterministic row id -------------------------------------------------


def test_row_id_is_deterministic_and_keyed_on_message_and_index():
    assert feed_row_id("msg-1", 0) == feed_row_id("msg-1", 0)
    assert feed_row_id("msg-1", 0) != feed_row_id("msg-1", 1)
    assert feed_row_id("msg-1", 0) != feed_row_id("msg-2", 0)


# ---- same-offer guard (duplicate Calculator feeds) ------------------------


def _prior(address, price, row_id="dd-old-0", verdict="Pass"):
    return PriorProperty(
        message_id="old",
        property_index=0,
        address=address,
        verdict=verdict,
        price=price,
        deals_app_row_id=row_id,
    )


def test_same_address_same_price_returns_the_existing_row_id():
    # The identical offer, re-sent: the Calculator already holds it.
    priors = [_prior("9 Oak Dr, Dallas TX", 250000)]
    assert already_fed(priors, 250000) == "dd-old-0"


def test_price_drop_is_a_new_offer_and_still_feeds():
    # The case the whole re-send breadcrumb exists for (US 27/28) — a price cut
    # reviving a dead deal must reach the Calculator.
    priors = [_prior("9 Oak Dr, Dallas TX", 250000)]
    assert already_fed(priors, 235000) == ""


def test_prior_that_was_never_fed_does_not_suppress():
    # A Rejected/not-calc-ready prior has no DEALS_APP row to collide with.
    priors = [_prior("9 Oak Dr, Dallas TX", 250000, row_id="")]
    assert already_fed(priors, 250000) == ""


def test_missing_current_price_never_claims_a_duplicate():
    priors = [_prior("9 Oak Dr, Dallas TX", 250000)]
    assert already_fed(priors, None) == ""


def test_prior_without_a_price_never_claims_a_duplicate():
    priors = [_prior("9 Oak Dr, Dallas TX", None)]
    assert already_fed(priors, 250000) == ""


def test_no_priors_feeds_normally():
    assert already_fed([], 250000) == ""


def test_price_comparison_tolerates_string_and_float_spellings():
    # Sheet reads come back as floats, extracted facts as ints or "$250,000".
    priors = [_prior("9 Oak Dr, Dallas TX", 250000.0)]
    assert already_fed(priors, "$250,000") == "dd-old-0"


def test_most_recent_matching_prior_wins():
    priors = [
        _prior("9 Oak Dr", 250000, row_id="dd-first-0"),
        _prior("9 Oak Dr", 250000, row_id="dd-latest-0"),
    ]
    assert already_fed(priors, 250000) == "dd-latest-0"


def test_matching_price_is_found_past_an_unfed_prior():
    priors = [
        _prior("9 Oak Dr", 250000, row_id="dd-fed-0"),
        _prior("9 Oak Dr", 250000, row_id=""),
    ]
    assert already_fed(priors, 250000) == "dd-fed-0"


# ---- sellerAgent ----------------------------------------------------------


def test_seller_agent_is_written_into_the_deal():
    deal = build_deal_input(_full_fields(), ASSUMPTIONS, seller_agent="Momentum Capital")
    assert deal["sellerAgent"] == "Momentum Capital"


def test_seller_agent_key_is_present_even_when_unknown():
    """The Calculator merges over DEFAULT_DEAL, so an *omitted* key inherits its
    example agent ("BSJ") — a fabricated fact on every triaged deal. Explicit ""
    is the Calculator's own blank."""
    deal = build_deal_input(_full_fields(), ASSUMPTIONS)
    assert "sellerAgent" in deal
    assert deal["sellerAgent"] == ""


def test_seller_agent_is_trimmed():
    deal = build_deal_input(_full_fields(), ASSUMPTIONS, seller_agent="  Reed Hunter  ")
    assert deal["sellerAgent"] == "Reed Hunter"


def test_non_string_seller_agent_degrades_to_blank_not_a_crash():
    deal = build_deal_input(_full_fields(), ASSUMPTIONS, seller_agent=None)
    assert deal["sellerAgent"] == ""


# ---- sqft -----------------------------------------------------------------


def test_sqft_is_carried_into_the_deal():
    fields = dict(_full_fields(), sqft=1868)
    assert build_deal_input(fields, ASSUMPTIONS)["sqft"] == 1868


def test_missing_sqft_is_written_as_zero_sentinel_never_omitted():
    """An omitted key inherits the Calculator's example area (1,621 sq ft), so a
    deal with no stated size would silently claim one."""
    deal = build_deal_input(_full_fields(), ASSUMPTIONS)
    assert deal["sqft"] == 0


def test_unparseable_sqft_is_written_as_zero_sentinel():
    fields = dict(_full_fields(), sqft="call for details")
    assert build_deal_input(fields, ASSUMPTIONS)["sqft"] == 0
