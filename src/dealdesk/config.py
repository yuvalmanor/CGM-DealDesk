"""Config loader.

The operator authors a single TOML file (see ``config/dealdesk.toml``). Phase 1
consumes the inbox address, the activation cutoff, the Bucket labels that mark an
Email as already processed, and the auth wiring. The Buy Box field catalog is a
later-phase concern and is not modelled here.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

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
        )
