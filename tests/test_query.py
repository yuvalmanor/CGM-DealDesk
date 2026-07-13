from datetime import date

from dealdesk.query import build_work_queue_query

LABELS = ("Passed-BuyBox", "Needs-Human", "Rejected", "Not-A-Deal", "Error")


def test_query_scopes_to_inbox_and_cutoff():
    q = build_work_queue_query(date(2026, 6, 28), LABELS)
    assert "in:inbox" in q
    assert "after:2026/06/28" in q


def test_query_excludes_every_bucket_label():
    q = build_work_queue_query(date(2026, 6, 28), LABELS)
    for label in LABELS:
        assert f"-label:{label}" in q


def test_changing_cutoff_changes_query():
    early = build_work_queue_query(date(2026, 1, 1), LABELS)
    late = build_work_queue_query(date(2026, 6, 28), LABELS)
    assert early != late
    assert "after:2026/01/01" in early


def test_label_with_whitespace_is_quoted():
    q = build_work_queue_query(date(2026, 6, 28), ("Deal Notifications",))
    assert '-label:"Deal Notifications"' in q
