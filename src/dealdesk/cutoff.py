"""Activation-cutoff resolution (pure).

Only Emails received on/after the cutoff are processed. An explicit config date
wins so the operator can move it back deliberately to sweep older backlog;
otherwise the cutoff floats at (today - lookback_days).
"""

from __future__ import annotations

from datetime import date, timedelta

from .config import Config


def resolve_cutoff(config: Config, today: date) -> date:
    """Return the activation cutoff date.

    Explicit ``activation.cutoff`` takes precedence; absent it, fall back to a
    rolling window ``lookback_days`` before ``today``.
    """
    if config.cutoff is not None:
        return config.cutoff
    return today - timedelta(days=config.lookback_days)
