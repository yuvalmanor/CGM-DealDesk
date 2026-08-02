from datetime import date

from dealdesk.config import Config


def test_load_default_config():
    cfg = Config.load()
    assert cfg.inbox_address == "deals@cgm-ventures.com"
    assert cfg.cutoff == date(2026, 7, 13)
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


def test_notify_deal_cc_defaults_empty_and_parses():
    # Absent -> no extra recipient; present -> carried through.
    assert Config.from_dict({"inbox": {"address": "x@y.com"}}).notify_deal_cc == ""
    cfg = Config.from_dict(
        {"inbox": {"address": "x@y.com"}, "notify": {"deal_cc": "extra@example.com"}}
    )
    assert cfg.notify_deal_cc == "extra@example.com"


def test_escalate_after_days_defaults_and_overrides():
    assert Config.load().escalate_after_days == 3  # shipped default
    cfg = Config.from_dict({"inbox": {"address": "x@y.com"}, "retry": {"escalate_after_days": 7}})
    assert cfg.escalate_after_days == 7


def test_calc_enabled_defaults_true_and_parses():
    # Absent -> feed on (an older config keeps its behavior); explicit false wins.
    assert Config.from_dict({"inbox": {"address": "x@y.com"}}).calc_enabled is True
    cfg = Config.from_dict(
        {"inbox": {"address": "x@y.com"}, "calculator": {"enabled": False}}
    )
    assert cfg.calc_enabled is False


def test_shipped_config_keeps_effects_inside_the_deals_mailbox():
    """The two exceptions the operator set on 2026-08-01 while the pipeline is
    reworked: no Calculator insertion, and no email leaving the deals mailbox.
    The rest of the pipeline runs normally. Pinned so neither is restored by
    accident — reversing one is a deliberate edit here plus the config."""
    cfg = Config.load()
    assert cfg.calc_enabled is False        # no DEALS_APP row is written
    assert cfg.notify_deal_cc == ""         # no external CC recipient
    # Notifications are send-to-self: everything stays in the deals mailbox.
    assert cfg.notify_to == cfg.inbox_address
    assert cfg.notify_from == cfg.inbox_address


# ---- self-addresses (the self-ingestion exclusion) ------------------------


def test_self_addresses_covers_inbox_and_notify_from():
    cfg = Config.from_dict(
        {"inbox": {"address": "deals@x.com"}, "notify": {"from": "bot@x.com"}}
    )
    assert cfg.self_addresses == ("deals@x.com", "bot@x.com")


def test_self_addresses_deduplicates_when_notify_from_is_the_inbox():
    # The shipped config: notify.from defaults to the inbox, so there is one
    # address to exclude, not the same one twice.
    cfg = Config.from_dict({"inbox": {"address": "deals@x.com"}})
    assert cfg.self_addresses == ("deals@x.com",)


def test_self_addresses_are_lowercased():
    cfg = Config.from_dict({"inbox": {"address": "Deals@X.com"}})
    assert cfg.self_addresses == ("deals@x.com",)


def test_default_config_excludes_the_deals_mailbox():
    assert "deals@cgm-ventures.com" in Config.load().self_addresses
