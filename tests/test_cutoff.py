from datetime import date

from dealdesk.config import Config
from dealdesk.cutoff import resolve_cutoff


def _cfg(cutoff=None, lookback_days=14):
    return Config(
        inbox_address="x@y.com",
        cutoff=cutoff,
        lookback_days=lookback_days,
        bucket_labels=(),
        credential_env="GOOGLE_SERVICE_ACCOUNT_KEY",
        delegated_subject="x@y.com",
    )


def test_explicit_cutoff_wins():
    cfg = _cfg(cutoff=date(2026, 6, 28))
    # 'today' is ignored when an explicit cutoff is set.
    assert resolve_cutoff(cfg, date(2026, 7, 13)) == date(2026, 6, 28)


def test_rolling_cutoff_when_unset():
    cfg = _cfg(cutoff=None, lookback_days=14)
    assert resolve_cutoff(cfg, date(2026, 7, 13)) == date(2026, 6, 29)


def test_operator_moves_cutoff_back_to_sweep_backlog():
    # A deliberately older explicit date sweeps more history.
    recent = _cfg(cutoff=date(2026, 6, 28))
    swept = _cfg(cutoff=date(2026, 1, 1))
    today = date(2026, 7, 13)
    assert resolve_cutoff(swept, today) < resolve_cutoff(recent, today)
