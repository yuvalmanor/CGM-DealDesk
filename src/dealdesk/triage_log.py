"""Triage Log row — the per-Property system of record.

One row per Property in the ``DEALS_TRIAGE`` sheet. Keyed on (message-id,
property index) so the upsert is idempotent across a crash/retry. A re-sent
Property gets its own new row like any other — rows are never merged, so the
address appearing twice is a feature (the re-send breadcrumb points back), not a
duplicate to collapse.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .evaluator import Evaluation
from .models import Email
from .source import derive_source

# Column order — key columns (A, B) first so the gateway can read them cheaply.
HEADER = (
    "message_id",
    "property_index",
    "received_date",
    "source",
    "address",
    "facts_json",
    "verdict",
    "reasons",
    "calc_ready",
    "missing_fields",
    "resend_flag",
    "deals_app_row_id",
    "notified",
)


@dataclass(frozen=True)
class TriageRow:
    message_id: str
    property_index: int
    received_date: str
    source: str
    address: str
    facts_json: str
    verdict: str
    reasons: str
    calc_ready: bool
    missing_fields: str
    resend_flag: str = ""
    deals_app_row_id: str = ""
    notified: bool = False

    @property
    def key(self) -> tuple[str, str]:
        """The idempotency key as it appears in the sheet's A/B columns."""
        return (self.message_id, str(self.property_index))

    def to_values(self) -> list[str]:
        return [
            self.message_id,
            str(self.property_index),
            self.received_date,
            self.source,
            self.address,
            self.facts_json,
            self.verdict,
            self.reasons,
            "TRUE" if self.calc_ready else "FALSE",
            self.missing_fields,
            self.resend_flag,
            self.deals_app_row_id,
            "TRUE" if self.notified else "FALSE",
        ]


def build_triage_row(
    email: Email,
    index: int,
    fields: dict,
    evaluation: Evaluation,
    deals_app_row_id: str = "",
    notified: bool = False,
    resend_flag: str = "",
) -> TriageRow:
    return TriageRow(
        message_id=email.id,
        property_index=index,
        received_date=email.date,
        source=derive_source(email.from_addr),
        address=str(fields.get("address", "")),
        # The extracted facts, and only those — the re-send breadcrumb is
        # DealDesk's own annotation, so it gets its own column rather than
        # polluting the record of what the Source actually said.
        facts_json=json.dumps(fields, sort_keys=True),
        verdict=evaluation.verdict.value,
        reasons="; ".join(evaluation.reasons),
        calc_ready=evaluation.calc_ready,
        missing_fields="; ".join(evaluation.missing_fields),
        resend_flag=resend_flag,
        deals_app_row_id=deals_app_row_id,
        notified=notified,
    )
