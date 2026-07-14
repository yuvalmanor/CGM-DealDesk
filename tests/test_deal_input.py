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
    build_deal_input,
    feed_row_id,
    should_feed,
)
from dealdesk.evaluator import Evaluation
from dealdesk.models import Verdict

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


# ---- feed routing rule ----------------------------------------------------


def _eval(verdict, calc_ready):
    return Evaluation(verdict, (), calc_ready, ())


def test_calc_ready_pass_is_fed():
    assert should_feed(_eval(Verdict.PASS, True)) is True


def test_calc_ready_needs_human_is_fed():
    assert should_feed(_eval(Verdict.NEEDS_HUMAN, True)) is True


def test_reject_is_never_fed_even_when_calc_ready():
    assert should_feed(_eval(Verdict.REJECT, True)) is False


def test_not_calc_ready_is_not_fed():
    assert should_feed(_eval(Verdict.PASS, False)) is False


# ---- deterministic row id -------------------------------------------------


def test_row_id_is_deterministic_and_keyed_on_message_and_index():
    assert feed_row_id("msg-1", 0) == feed_row_id("msg-1", 0)
    assert feed_row_id("msg-1", 0) != feed_row_id("msg-1", 1)
    assert feed_row_id("msg-1", 0) != feed_row_id("msg-2", 0)
