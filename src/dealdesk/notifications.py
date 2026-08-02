"""Notification Builder (pure, deep module).

Turns triage results into email *content* — no I/O. Two products:

- **Deal Notification** — one per ``Passed-BuyBox`` Property (Verdict ``Pass``),
  carrying address, Source, price/rent/ARV, the Verdict + why, a link to the
  Calculator row, and any re-send breadcrumb. The operator's existing Gmail
  filter routes it by its ``deals@cgm-ventures.com`` From address (set by the
  gateway on send).
- **Daily Digest** — one per run: per-Bucket counts and one-line listings across
  every Bucket (incl. ``Rejected``/``Error``), with ``Needs-Human`` Properties
  listed alongside *what's missing* (they get no per-deal email).

Both return a plain ``Notification`` (subject + text body) so the content is
golden-testable through this interface without touching Gmail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from .address import address_label
from .evaluator import Evaluation
from .models import Bucket, Verdict
from .source import derive_source

if TYPE_CHECKING:  # avoid a runtime import cycle (orchestrator imports this module)
    from .orchestrator import EmailResult

# Bucket order for the digest listings — winners first, junk/retry last.
_DIGEST_BUCKET_ORDER = (
    Bucket.PASSED_BUYBOX,
    Bucket.NEEDS_HUMAN,
    Bucket.REJECTED,
    Bucket.NOT_A_DEAL,
    Bucket.ERROR,
)

# Buckets whose Emails carry Properties (listed per-Property in the digest).
_PROPERTY_BUCKETS = (Bucket.PASSED_BUYBOX, Bucket.NEEDS_HUMAN, Bucket.REJECTED)


@dataclass(frozen=True)
class Notification:
    """A ready-to-send email: subject line + plain-text body."""

    subject: str
    body: str


def build_deal_notification(
    fields: dict,
    evaluation: Evaluation,
    source: str,
    calc_link: str,
    resend_flag: str = "",
    email_subject: str = "",
    email_sender: str = "",
) -> Notification:
    """Per-deal notification for a ``Passed-BuyBox`` Property. Everything the
    operator needs to judge it without opening anything else.

    ``email_subject``/``email_sender`` name the Email the Property came from; with
    no extracted address they stand in for it (``"<subject>|<sender>"``), so the
    subject line still identifies the deal well enough to search Gmail for."""
    address = address_label(fields.get("address"), email_subject, email_sender) or "(address unknown)"
    subject = f"Deal: {address} — {source}"

    lines = [
        f"Address:    {address}",
        f"Source:     {source}",
        f"Price:      {_money(fields.get('purchase_price'))}",
        f"Rent:       {_money(fields.get('monthly_rent'))}",
        f"ARV:        {_arv(fields.get('arv'))}",
        f"Verdict:    {evaluation.verdict.value}",
        f"Why:        {'; '.join(evaluation.reasons) or '(no reasons)'}",
        f"Calculator: {calc_link}",
    ]
    if resend_flag:
        lines.append(f"Re-send:    {resend_flag}")
    return Notification(subject=subject, body="\n".join(lines))


def build_digest(results: Sequence["EmailResult"]) -> Notification:
    """One run summary: per-Bucket counts and one-line listings across every
    Bucket. ``Needs-Human`` Properties carry *what's missing*."""
    counts = {b: 0 for b in _DIGEST_BUCKET_ORDER}
    for r in results:
        counts[r.bucket] = counts.get(r.bucket, 0) + 1

    total = len(results)
    passed = counts[Bucket.PASSED_BUYBOX]
    needs = counts[Bucket.NEEDS_HUMAN]
    subject = f"DealDesk daily digest — {total} email(s), {passed} passed, {needs} need(s) human"

    body: list[str] = ["Counts:"]
    for bucket in _DIGEST_BUCKET_ORDER:
        body.append(f"  {bucket.value:<14} {counts[bucket]}")

    for bucket in _DIGEST_BUCKET_ORDER:
        rows = [r for r in results if r.bucket is bucket]
        if not rows:
            continue
        body.append("")
        body.append(f"{bucket.value}:")
        for r in rows:
            body.extend(_digest_lines(r, bucket))

    return Notification(subject=subject, body="\n".join(body))


def _digest_lines(result: "EmailResult", bucket: Bucket) -> list[str]:
    source = derive_source(result.sender)
    if bucket not in _PROPERTY_BUCKETS or not result.properties:
        # No-Property Buckets (Not-A-Deal / Error) list per Email.
        subject = result.subject or "(no subject)"
        tail = f" — {result.error}" if result.error else ""
        return [f"  - {subject} ({source}){tail}"]

    lines: list[str] = []
    for fields, ev in result.properties:
        address = (
            address_label(fields.get("address"), result.subject, result.sender)
            or "(address unknown)"
        )
        if ev.verdict is Verdict.NEEDS_HUMAN:
            missing = ", ".join(ev.missing_fields) or "(unknown)"
            lines.append(f"  - {address} ({source}) — missing {missing}")
        else:
            reason = "; ".join(ev.reasons)
            suffix = f" — {reason}" if reason else ""
            lines.append(f"  - {address} ({source}) — {ev.verdict.value}{suffix}")
    return lines


def _money(value: object) -> str:
    n = _to_number(value)
    return "unknown" if n is None else f"${n:,.0f}"


def _arv(value: object) -> str:
    n = _to_number(value)
    return "unknown (fill in)" if n is None else f"${n:,.0f}"


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


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
