"""Buy Box Evaluator (pure, deep module).

``evaluate(property_fields, buybox) -> Evaluation`` — the crisp decision logic
DealDesk keeps deterministic and unit-testable (per ADR-0001). Only filter-role
fields decide the Verdict:

- a gate field present and failing → **Reject** (a confident Reject wins, and
  short-circuits any missing data);
- else a gate field missing → **Needs-Human**;
- else all gates pass → **Pass**.

Calc-ready is independent of the Verdict: all feed-required inputs present.
Rent-to-price yield is never modelled as a gate, so it can never be a rejection
reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from .buybox import BuyBox, FieldSpec
from .models import Verdict

_MEMBERSHIP_OPS = {"in", "not_in"}


@dataclass(frozen=True)
class Evaluation:
    verdict: Verdict
    reasons: tuple[str, ...]
    calc_ready: bool
    missing_fields: tuple[str, ...]  # missing filter (gate) fields — the "what's missing"


def evaluate(property_fields: dict, buybox: BuyBox) -> Evaluation:
    reject_reasons: list[str] = []
    missing_filter: list[str] = []

    for spec in buybox.gate_fields():
        value = property_fields.get(spec.name)
        if _is_missing(value):
            missing_filter.append(spec.name)
            continue
        passed, reason = _check_gate(spec, value)
        if not passed:
            if reason is None:
                # Present but un-evaluable (e.g. a non-numeric value on a numeric
                # gate) — treat as missing rather than fabricate a Reject.
                missing_filter.append(spec.name)
            else:
                reject_reasons.append(reason)

    calc_ready = all(not _is_missing(property_fields.get(n)) for n in buybox.feed_required_names())

    if reject_reasons:
        # Reject short-circuits missing data: a confident no doesn't wait on a human.
        return Evaluation(Verdict.REJECT, tuple(reject_reasons), calc_ready, ())
    if missing_filter:
        reasons = tuple(f"missing {name}" for name in missing_filter)
        return Evaluation(Verdict.NEEDS_HUMAN, reasons, calc_ready, tuple(missing_filter))
    return Evaluation(Verdict.PASS, ("all Buy Box gates pass",), calc_ready, ())


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _check_gate(spec: FieldSpec, value: object) -> tuple[bool, str | None]:
    """Return (passed, fail_reason). fail_reason is None when the value could
    not be evaluated (caller treats that as missing)."""
    if spec.op in _MEMBERSHIP_OPS:
        return _check_membership(spec, value)
    return _check_numeric(spec, value)


def _check_membership(spec: FieldSpec, value: object) -> tuple[bool, str | None]:
    allowed = {str(v).strip().lower() for v in spec.threshold or ()}
    normalized = str(value).strip().lower()
    inside = normalized in allowed
    passes = inside if spec.op == "in" else not inside
    if passes:
        return True, None
    verb = "not in" if spec.op == "in" else "in"
    return False, f"{spec.name} '{value}' {verb} Buy Box list"


def _check_numeric(spec: FieldSpec, value: object) -> tuple[bool, str | None]:
    parsed = _to_number(value)
    if parsed is None:
        return False, None
    threshold = _to_number(spec.threshold)
    if threshold is None:
        return False, None
    ops = {
        ">=": parsed >= threshold,
        "<=": parsed <= threshold,
        ">": parsed > threshold,
        "<": parsed < threshold,
        "==": parsed == threshold,
    }
    if ops[spec.op]:
        return True, None
    return False, f"{spec.name} {_fmt(parsed)} fails {spec.op} {_fmt(threshold)}"


def _to_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    text = str(value).strip().lstrip("$").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _fmt(n: float) -> str:
    return str(int(n)) if n == int(n) else str(n)
