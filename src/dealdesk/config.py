"""Config loader.

The operator authors a single TOML file (see ``config/dealdesk.toml``). It
consumes the inbox address, the activation cutoff, the Bucket labels, the auth
wiring, the Triage Log sheet, the AI-fallback settings, and the **Buy Box field
catalog** (each field's filter/calc role and gate threshold). The catalog is
authored by the operator, never hard-coded.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .buybox import BuyBox
from .deal_input import Assumptions

# Repo-root-relative default, resolved from this file's location so the entry
# command works regardless of the caller's working directory.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "dealdesk.toml"


@dataclass(frozen=True)
class Config:
    inbox_address: str
    cutoff: date | None
    lookback_days: int
    bucket_labels: tuple[str, ...]
    credential_env: str
    delegated_subject: str
    buybox: BuyBox = field(default_factory=BuyBox)
    triage_spreadsheet_id: str = ""
    triage_tab: str = "DEALS_TRIAGE"
    calc_spreadsheet_id: str = ""
    calc_tab: str = "DEALS_APP"
    # Kill switch for the downstream Calculator feed. When false, DealDesk still
    # triages, logs, labels, and notifies — it just never writes a Deal into
    # DEALS_APP, and no Triage row claims a fed row id. Kept separate from
    # `calc_spreadsheet_id` so turning the feed off doesn't require destroying the
    # sink wiring (and turning it back on is a one-word edit).
    calc_enabled: bool = True
    assumptions: Assumptions = field(default_factory=lambda: Assumptions(0.0, 0.0))
    ai_model: str = "claude-opus-4-8"
    ai_api_key_env: str = "ANTHROPIC_API_KEY"
    notify_to: str = ""
    notify_from: str = ""
    notify_label: str = ""
    notify_deal_cc: str = ""
    escalate_after_days: int = 3

    @property
    def self_addresses(self) -> tuple[str, ...]:
        """Every address DealDesk itself sends as — excluded from the work queue
        so the pipeline never triages its own Deal Notifications and Daily Digest
        (see ``query.build_work_queue_query``).

        Both the watched inbox and ``notify.from`` are listed: they are the same
        address in the shipped config, but ``notify.from`` is operator-settable,
        and an operator who points it elsewhere would silently re-open the loop.
        Derived here rather than at the call site so there is one definition of
        "our own mail"."""
        candidates = (self.inbox_address, self.notify_from)
        seen: list[str] = []
        for raw in candidates:
            address = (raw or "").strip().lower()
            if address and address not in seen:
                seen.append(address)
        return tuple(seen)

    @property
    def calc_link(self) -> str:
        """Deep-ish link to the Calculator's sheet used in Deal Notifications.
        Empty when no Calculator spreadsheet is configured."""
        if not self.calc_spreadsheet_id:
            return ""
        return f"https://docs.google.com/spreadsheets/d/{self.calc_spreadsheet_id}/edit"

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "Config":
        inbox = raw.get("inbox", {})
        activation = raw.get("activation", {})
        buckets = raw.get("buckets", {})
        auth = raw.get("auth", {})
        triage = raw.get("triage", {})
        calculator = raw.get("calculator", {})
        ai = raw.get("ai", {})
        notify = raw.get("notify", {})
        retry = raw.get("retry", {})

        cutoff_raw = activation.get("cutoff")
        # tomllib parses a bare TOML date into a datetime.date already; accept a
        # string too so a hand-edited value still works.
        if isinstance(cutoff_raw, str):
            cutoff = date.fromisoformat(cutoff_raw)
        elif isinstance(cutoff_raw, date):
            cutoff = cutoff_raw
        elif cutoff_raw is None:
            cutoff = None
        else:
            raise ValueError(f"activation.cutoff must be a date or ISO string, got {cutoff_raw!r}")

        return cls(
            inbox_address=inbox["address"],
            cutoff=cutoff,
            lookback_days=int(activation.get("lookback_days", 14)),
            bucket_labels=tuple(buckets.get("labels", [])),
            credential_env=auth.get("credential_env", "GOOGLE_SERVICE_ACCOUNT_KEY"),
            delegated_subject=auth.get("delegated_subject", inbox["address"]),
            buybox=BuyBox.from_dict(raw.get("buybox")),
            triage_spreadsheet_id=triage.get("spreadsheet_id", ""),
            triage_tab=triage.get("tab", "DEALS_TRIAGE"),
            calc_spreadsheet_id=calculator.get("spreadsheet_id", ""),
            calc_tab=calculator.get("tab", "DEALS_APP"),
            # Absent = enabled, so an older config keeps its behavior.
            calc_enabled=bool(calculator.get("enabled", True)),
            assumptions=Assumptions(
                hml_lev_pp=float(calculator.get("hml_lev_pp", 0.0)),
                refi_ltv=float(calculator.get("refi_ltv", 0.0)),
            ),
            ai_model=ai.get("model", "claude-opus-4-8"),
            ai_api_key_env=ai.get("api_key_env", "ANTHROPIC_API_KEY"),
            notify_to=notify.get("to", ""),
            # Deal Notifications must come *from* the deals mailbox so the
            # operator's existing filter routes them; default to the inbox.
            notify_from=notify.get("from", inbox["address"]),
            notify_label=notify.get("label", ""),
            # An extra recipient CC'd on every per-Property Deal Notification (the
            # Daily Digest is unaffected). Empty = no CC. Used to fan a Passed deal
            # out to an external inbox alongside the send-to-self copy.
            notify_deal_cc=notify.get("deal_cc", ""),
            # Age (days) past which a still-failing Error Email escalates to
            # Needs-Human — age stands in for a retry counter (Phase 5).
            escalate_after_days=int(retry.get("escalate_after_days", 3)),
        )
