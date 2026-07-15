from datetime import date

from dealdesk.config import Config


def test_load_default_config():
    cfg = Config.load()
    assert cfg.inbox_address == "deals@cgm-ventures.com"
    assert cfg.cutoff == date(2026, 6, 28)
    assert cfg.lookback_days == 14
    assert cfg.bucket_labels == (
        "Passed-BuyBox",
        "Needs-Human",
        "Rejected",
        "Not-A-Deal",
        "Error",
    )
    assert cfg.credential_env == "GOOGLE_SERVICE_ACCOUNT_KEY"
    assert cfg.delegated_subject == "deals@cgm-ventures.com"


def test_from_dict_accepts_string_cutoff():
    cfg = Config.from_dict(
        {
            "inbox": {"address": "x@y.com"},
            "activation": {"cutoff": "2026-01-01", "lookback_days": 30},
        }
    )
    assert cfg.cutoff == date(2026, 1, 1)
    assert cfg.lookback_days == 30


def test_from_dict_cutoff_optional():
    cfg = Config.from_dict({"inbox": {"address": "x@y.com"}, "activation": {}})
    assert cfg.cutoff is None
    assert cfg.lookback_days == 14  # default


def test_delegated_subject_defaults_to_inbox():
    cfg = Config.from_dict({"inbox": {"address": "x@y.com"}})
    assert cfg.delegated_subject == "x@y.com"


def test_escalate_after_days_defaults_and_overrides():
    assert Config.load().escalate_after_days == 3  # shipped default
    cfg = Config.from_dict({"inbox": {"address": "x@y.com"}, "retry": {"escalate_after_days": 7}})
    assert cfg.escalate_after_days == 7
