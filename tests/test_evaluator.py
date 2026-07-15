"""Golden/table tests for the Buy Box Evaluator decision matrix."""

from dealdesk.buybox import BuyBox, FieldSpec
from dealdesk.evaluator import evaluate
from dealdesk.models import Verdict

# A representative Buy Box: two numeric gates, one membership gate, one
# feed-required non-gate (rent), one feed-optional non-gate (ARV).
BUYBOX = BuyBox(
    fields=(
        FieldSpec("purchase_price", "gate", "feed-required", "<=", 300000),
        FieldSpec("year_built", "gate", "none", ">=", 2000),
        FieldSpec("city", "gate", "none", "in", ["dallas", "fort worth"]),
        FieldSpec("monthly_rent", "none", "feed-required"),
        FieldSpec("arv", "none", "feed-optional"),
    )
)


def _base(**overrides):
    fields = {
        "purchase_price": 250000,
        "year_built": 2010,
        "city": "Dallas",
        "monthly_rent": 2000,
        "arv": 320000,
    }
    fields.update(overrides)
    return fields


def test_all_gates_pass_is_pass():
    ev = evaluate(_base(), BUYBOX)
    assert ev.verdict is Verdict.PASS
    assert ev.calc_ready is True
    assert ev.missing_fields == ()


def test_present_and_fails_gate_is_reject():
    ev = evaluate(_base(year_built=1960), BUYBOX)
    assert ev.verdict is Verdict.REJECT
    assert any("year_built" in r for r in ev.reasons)


def test_price_over_band_is_reject():
    ev = evaluate(_base(purchase_price=400000), BUYBOX)
    assert ev.verdict is Verdict.REJECT
    assert any("purchase_price" in r for r in ev.reasons)


def test_city_outside_box_is_reject():
    ev = evaluate(_base(city="Houston"), BUYBOX)
    assert ev.verdict is Verdict.REJECT
    assert any("Houston" in r for r in ev.reasons)


def test_missing_filter_field_is_needs_human():
    fields = _base()
    del fields["city"]
    ev = evaluate(fields, BUYBOX)
    assert ev.verdict is Verdict.NEEDS_HUMAN
    assert ev.missing_fields == ("city",)


def test_confident_reject_short_circuits_missing_data():
    # year fails AND city is missing — a confident Reject wins over Needs-Human.
    fields = _base(year_built=1960)
    del fields["city"]
    ev = evaluate(fields, BUYBOX)
    assert ev.verdict is Verdict.REJECT


def test_feed_required_missing_is_not_calc_ready_but_can_still_pass():
    fields = _base()
    del fields["monthly_rent"]
    ev = evaluate(fields, BUYBOX)
    assert ev.verdict is Verdict.PASS  # rent is not a gate
    assert ev.calc_ready is False


def test_feed_optional_missing_is_still_calc_ready():
    fields = _base()
    del fields["arv"]
    ev = evaluate(fields, BUYBOX)
    assert ev.verdict is Verdict.PASS
    assert ev.calc_ready is True


def test_low_yield_is_never_a_rejection_reason():
    # A poor rent-to-price ratio (low yield) must not reject — rent is not a gate.
    ev = evaluate(_base(purchase_price=290000, monthly_rent=800), BUYBOX)
    assert ev.verdict is Verdict.PASS


def test_unparseable_gate_value_is_treated_as_missing():
    ev = evaluate(_base(year_built="unknown"), BUYBOX)
    assert ev.verdict is Verdict.NEEDS_HUMAN
    assert "year_built" in ev.missing_fields


# --- property-type gate (SFH-only allowlist) -------------------------------
# A membership gate: only `single_family` passes; any other present type Rejects;
# a missing type is Needs-Human. Mirrors the shipped config.
TYPED_BUYBOX = BuyBox(
    fields=(
        FieldSpec("property_type", "gate", "none", "in", ["single_family"]),
        FieldSpec("purchase_price", "gate", "feed-required", "<=", 350000),
        FieldSpec("year_built", "gate", "none", ">=", 1995),
        FieldSpec("monthly_rent", "none", "feed-required"),
    )
)


def test_single_family_passes_the_type_gate():
    fields = {"property_type": "single_family", "purchase_price": 250000, "year_built": 2010, "monthly_rent": 2000}
    assert evaluate(fields, TYPED_BUYBOX).verdict is Verdict.PASS


def test_lot_rejects_and_short_circuits_missing_year_built():
    # The real-world case: a vacant lot has no year_built, but the wrong type is a
    # confident Reject that must win over Needs-Human — so lots auto-file, never
    # waiting on a human. (year_built deliberately absent.)
    fields = {"property_type": "vacant_lot", "purchase_price": 90000}
    ev = evaluate(fields, TYPED_BUYBOX)
    assert ev.verdict is Verdict.REJECT
    assert any("property_type" in r for r in ev.reasons)


def test_mobile_home_rejects_on_type_gate():
    fields = {"property_type": "mobile_home", "purchase_price": 120000, "year_built": 2005, "monthly_rent": 1500}
    assert evaluate(fields, TYPED_BUYBOX).verdict is Verdict.REJECT


def test_missing_property_type_is_needs_human():
    fields = {"purchase_price": 250000, "year_built": 2010, "monthly_rent": 2000}
    ev = evaluate(fields, TYPED_BUYBOX)
    assert ev.verdict is Verdict.NEEDS_HUMAN
    assert "property_type" in ev.missing_fields
