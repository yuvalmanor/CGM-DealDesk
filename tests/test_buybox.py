"""Buy Box catalog loading + must-have derivation."""

import pytest

from dealdesk.buybox import BuyBox
from dealdesk.config import Config

_RAW = {
    "fields": [
        {"name": "purchase_price", "filter": "gate", "op": "<=", "threshold": 300000, "calc": "feed-required"},
        {"name": "year_built", "filter": "gate", "op": ">=", "threshold": 2000},
        {"name": "city", "filter": "gate", "op": "in", "threshold": ["dallas"]},
        {"name": "monthly_rent", "calc": "feed-required"},
        {"name": "arv", "calc": "feed-optional"},
    ]
}


def test_must_have_names_are_gate_plus_feed_required_deduped():
    bb = BuyBox.from_dict(_RAW)
    # purchase_price is both a gate and feed-required — appears once.
    assert bb.must_have_names() == ("purchase_price", "year_built", "city", "monthly_rent")


def test_feed_role_accessors():
    bb = BuyBox.from_dict(_RAW)
    assert bb.feed_required_names() == ("purchase_price", "monthly_rent")
    assert bb.feed_optional_names() == ("arv",)
    assert tuple(f.name for f in bb.gate_fields()) == ("purchase_price", "year_built", "city")


def test_gate_without_op_is_rejected():
    with pytest.raises(ValueError):
        BuyBox.from_dict({"fields": [{"name": "x", "filter": "gate", "threshold": 1}]})


def test_gate_without_threshold_is_rejected():
    with pytest.raises(ValueError):
        BuyBox.from_dict({"fields": [{"name": "x", "filter": "gate", "op": ">="}]})


def test_config_loads_buybox_from_default_file():
    cfg = Config.load()
    # Operator-set catalog: price + year gates only (property_type gate dropped
    # 2026-07-18 so the ladder can short-circuit without an AI call), no location
    # gate. Must-haves are down to the two deterministically-recovered fields.
    #
    # This asserts the config->code *contract* — which fields gate, their roles,
    # and that every gate carries a usable op + threshold. It deliberately does
    # NOT pin the threshold *values*: those are operator-tunable (the shipped
    # numbers are the real Buy Box and change as criteria change), so pinning them
    # here would turn every legitimate retune into a red build. Value parsing is
    # covered by the fixture-based tests above.
    assert cfg.buybox.must_have_names() == (
        "purchase_price",
        "year_built",
    )
    assert {f.name for f in cfg.buybox.gate_fields()} == {
        "purchase_price",
        "year_built",
    }
    assert "property_type" not in {f.name for f in cfg.buybox.fields}
    for gate in cfg.buybox.gate_fields():
        assert gate.op in (">=", "<=", ">", "<", "==", "in", "not_in")
        assert gate.threshold is not None
    assert cfg.buybox.feed_required_names() == ("purchase_price",)
    assert cfg.buybox.feed_optional_names() == ("monthly_rent", "arv")
