"""Bucket Rollup (pure).

``roll_up([Verdict…]) -> Bucket`` — an Email carries exactly one Bucket, the
best of its Properties' Verdicts. Precedence: Pass > Needs-Human > Reject. An
Email with no Property (spam, newsletter, reply) rolls up to ``Not-A-Deal``.
"""

from __future__ import annotations

from typing import Iterable

from .models import Bucket, Verdict


def roll_up(verdicts: Iterable[Verdict]) -> Bucket:
    verdicts = list(verdicts)
    if not verdicts:
        return Bucket.NOT_A_DEAL
    if any(v == Verdict.PASS for v in verdicts):
        return Bucket.PASSED_BUYBOX
    if any(v == Verdict.NEEDS_HUMAN for v in verdicts):
        return Bucket.NEEDS_HUMAN
    return Bucket.REJECTED
