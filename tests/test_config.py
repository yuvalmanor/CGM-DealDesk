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


def test_shipped_config_runs_the_full_pipeline():
    """The full pipeline is restored (2026-08-02): the Calculator feed is back on
    after the 2026-08-01 rework window, during which it was off along with the
    external notification CC.

    Pinned the same way the paused state was — the shipped config's outward
    effects are asserted here, so switching one off (or on) is a deliberate edit
    in two places rather than a silent drift. What guards the feed now is not the
    kill switch but the routing rule: only a Verdict Pass is written."""
    cfg = Config.load()
    assert cfg.calc_enabled is True         # DEALS_APP rows are written again
    assert cfg.calc_spreadsheet_id          # ...and there is somewhere to write them
    assert cfg.notify_deal_cc               # Deal Notifications reach an outside inbox
    # Notifications still originate from and land in the deals mailbox; deal_cc is
    # the one recipient outside it.
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
