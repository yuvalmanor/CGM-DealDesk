"""Gateway integration test against a mocked Gmail API.

Asserts external behavior through the public interface: the query shape sent to
Gmail, pagination, header mapping, and — critically for Phase 1 — that the read
half never reaches for a mutating call.
"""

from datetime import date

import pytest

from dealdesk.gmail_gateway import GmailGateway

LABELS = ("Passed-BuyBox", "Needs-Human", "Rejected", "Not-A-Deal", "Error")


class _FakeListRequest:
    def __init__(self, pages, calls):
        self._pages = pages
        self._calls = calls

    def execute(self):
        # Return the page matching the requested token (recorded by the parent).
        token = self._calls[-1]["pageToken"]
        return self._pages[token]


class _FakeGetRequest:
    def __init__(self, detail):
        self._detail = detail

    def execute(self):
        return self._detail


class _FakeMessages:
    def __init__(self, pages, details):
        self._pages = pages          # {pageToken(None first): {messages, nextPageToken}}
        self._details = details      # {id: detail}
        self.list_calls = []
        self.get_calls = []
        self.mutating_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return _FakeListRequest(self._pages, self.list_calls)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return _FakeGetRequest(self._details[kwargs["id"]])

    # Any mutating call would be a Phase-1 violation; record if reached.
    def modify(self, **kwargs):
        self.mutating_calls.append(("modify", kwargs))
        raise AssertionError("read half must not call modify()")


class _FakeUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class _FakeService:
    def __init__(self, messages):
        self._users = _FakeUsers(messages)

    def users(self):
        return self._users


def _detail(msg_id, from_addr, subject, date_hdr):
    return {
        "id": msg_id,
        "payload": {
            "headers": [
                {"name": "From", "value": from_addr},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date_hdr},
            ]
        },
    }


def test_fetch_work_queue_query_shape_and_mapping():
    pages = {
        None: {"messages": [{"id": "a"}, {"id": "b"}]},
    }
    details = {
        "a": _detail("a", "Blast <deals@acme.com>", "5 houses", "Mon, 01 Jul 2026"),
        "b": _detail("b", "mls@ntreis.net", "New listing", "Tue, 02 Jul 2026"),
    }
    messages = _FakeMessages(pages, details)
    gateway = GmailGateway(_FakeService(messages))

    result = gateway.fetch_work_queue(date(2026, 6, 28), LABELS)

    # Query shape: inbox + cutoff + every bucket label negated.
    q = messages.list_calls[0]["q"]
    assert "in:inbox" in q and "after:2026/06/28" in q
    for label in LABELS:
        assert f"-label:{label}" in q

    # Header mapping.
    assert [m.id for m in result] == ["a", "b"]
    assert result[0].from_addr == "Blast <deals@acme.com>"
    assert result[0].subject == "5 houses"
    assert result[1].from_addr == "mls@ntreis.net"

    # Metadata-only fetch — no body/attachment download.
    assert messages.get_calls[0]["format"] == "metadata"
    # Read-only: nothing mutating was invoked.
    assert messages.mutating_calls == []


def test_fetch_work_queue_paginates():
    pages = {
        None: {"messages": [{"id": "a"}], "nextPageToken": "p2"},
        "p2": {"messages": [{"id": "b"}]},
    }
    details = {
        "a": _detail("a", "x@one.com", "s", "d"),
        "b": _detail("b", "y@two.com", "s", "d"),
    }
    messages = _FakeMessages(pages, details)
    gateway = GmailGateway(_FakeService(messages))

    result = gateway.fetch_work_queue(date(2026, 6, 28), LABELS)

    assert [m.id for m in result] == ["a", "b"]
    assert len(messages.list_calls) == 2
    assert messages.list_calls[1]["pageToken"] == "p2"


def test_empty_queue():
    messages = _FakeMessages({None: {"messages": []}}, {})
    gateway = GmailGateway(_FakeService(messages))
    assert gateway.fetch_work_queue(date(2026, 6, 28), LABELS) == []
