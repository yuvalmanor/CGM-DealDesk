"""Buy Box field catalog (config model, pure).

The operator authors the concrete field catalog in ``config/dealdesk.toml`` under
``[[buybox.fields]]``. Each field is tagged along two independent axes:

- **filter role** (``gate`` | ``none``) — whether the field gates the deal.
- **calc role** (``feed-required`` | ``feed-optional`` | ``none``) — whether the
  field is a Calculator input, and whether it must be present to feed.

A gate field also carries an operator + threshold. This module only models and
loads the catalog; the decision logic lives in ``evaluator.py``. "Must-have"
fields (the ones extraction must recover) are *derived* from this one catalog —
gate fields plus feed-required fields — so there is one config, not two.
"""

from __future__ import annotations

from dataclasses import dataclass

GATE = "gate"
FEED_REQUIRED = "feed-required"
FEED_OPTIONAL = "feed-optional"

_FILTER_ROLES = {GATE, "none"}
_CALC_ROLES = {FEED_REQUIRED, FEED_OPTIONAL, "none"}
_NUMERIC_OPS = {">=", "<=", ">", "<", "=="}
_MEMBERSHIP_OPS = {"in", "not_in"}


@dataclass(frozen=True)
class FieldSpec:
    """One Buy Box field: its two roles and, if it gates, its threshold."""

    name: str
    filter_role: str = "none"
    calc_role: str = "none"
    op: str | None = None
    threshold: object = None

    @property
    def is_gate(self) -> bool:
        return self.filter_role == GATE

    @property
    def is_feed_required(self) -> bool:
        return self.calc_role == FEED_REQUIRED

    @property
    def is_feed_optional(self) -> bool:
        return self.calc_role == FEED_OPTIONAL


@dataclass(frozen=True)
class BuyBox:
    """The active Buy Box — an ordered catalog of field specs."""

    fields: tuple[FieldSpec, ...] = ()

    def gate_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(f for f in self.fields if f.is_gate)

    def feed_required_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.is_feed_required)

    def feed_optional_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.is_feed_optional)

    def must_have_names(self) -> tuple[str, ...]:
        """Fields extraction must recover: every gate field plus every
        feed-required field (de-duplicated, order-preserving). This is the one
        derivation of "must-have" — filter and calc criteria share one config."""
        names: list[str] = []
        for f in self.fields:
            if (f.is_gate or f.is_feed_required) and f.name not in names:
                names.append(f.name)
        return tuple(names)

    @classmethod
    def from_dict(cls, raw: dict | None) -> "BuyBox":
        raw = raw or {}
        specs = tuple(_field_from_dict(entry) for entry in raw.get("fields", []))
        return cls(fields=specs)


def _field_from_dict(entry: dict) -> FieldSpec:
    name = entry["name"]
    filter_role = entry.get("filter", "none")
    calc_role = entry.get("calc", "none")
    op = entry.get("op")
    threshold = entry.get("threshold")

    if filter_role not in _FILTER_ROLES:
        raise ValueError(f"{name}: filter role must be one of {_FILTER_ROLES}, got {filter_role!r}")
    if calc_role not in _CALC_ROLES:
        raise ValueError(f"{name}: calc role must be one of {_CALC_ROLES}, got {calc_role!r}")
    if filter_role == GATE:
        if op not in _NUMERIC_OPS and op not in _MEMBERSHIP_OPS:
            raise ValueError(f"{name}: gate field needs a valid op, got {op!r}")
        if threshold is None:
            raise ValueError(f"{name}: gate field needs a threshold")

    return FieldSpec(
        name=name,
        filter_role=filter_role,
        calc_role=calc_role,
        op=op,
        threshold=threshold,
    )
