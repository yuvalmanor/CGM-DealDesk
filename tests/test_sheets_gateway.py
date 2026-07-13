"""Sheets Gateway upsert against a stateful fake Sheets API.

Asserts the external behavior: a new key appends, an existing key updates in
place, and a re-upsert of the same key never duplicates the row (the idempotency
that makes crash-recovery safe).
"""

import re

from dealdesk.sheets_gateway import SheetsGateway
from dealdesk.triage_log import HEADER, TriageRow


class _FakeValues:
    """In-memory model of a sheet tab: a list of full rows (row 0 = header)."""

    def __init__(self, store):
        self._store = store

    def get(self, spreadsheetId, range):
        # Return only the A:B key columns, mirroring the real request.
        rows = [[r[0] if len(r) > 0 else "", r[1] if len(r) > 1 else ""] for r in self._store["rows"]]
        return _Exec({"values": rows})

    def update(self, spreadsheetId, range, valueInputOption, body):
        rownum = _row_of(range)
        values = body["values"][0]
        while len(self._store["rows"]) < rownum:
            self._store["rows"].append([])
        self._store["rows"][rownum - 1] = values
        return _Exec({})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self._store["rows"].append(body["values"][0])
        return _Exec({})


class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeSpreadsheets:
    def __init__(self, store):
        self._values = _FakeValues(store)

    def values(self):
        return self._values


class _FakeSheetsService:
    def __init__(self):
        self.store = {"rows": []}
        self._spreadsheets = _FakeSpreadsheets(self.store)

    def spreadsheets(self):
        return self._spreadsheets


def _row_of(rng: str) -> int:
    # "TAB!A2:M2" or "TAB!A1" -> 2 / 1
    return int(re.search(r"![A-Z]+(\d+)", rng).group(1))


def _row(msg_id, index, verdict="Pass"):
    return TriageRow(
        message_id=msg_id,
        property_index=index,
        received_date="d",
        source="acme.com",
        address="9 Oak Dr",
        facts_json="{}",
        verdict=verdict,
        reasons="",
        calc_ready=True,
        missing_fields="",
    )


def _data_rows(service):
    rows = service.store["rows"]
    return [r for r in rows if r and r[0] != HEADER[0]]


def test_first_upsert_writes_header_and_appends():
    service = _FakeSheetsService()
    gw = SheetsGateway(service, "sid", "DEALS_TRIAGE")

    gw.upsert_rows([_row("m1", 0)])

    assert service.store["rows"][0] == list(HEADER)
    assert len(_data_rows(service)) == 1


def test_reupsert_same_key_updates_in_place_no_duplicate():
    service = _FakeSheetsService()
    gw = SheetsGateway(service, "sid", "DEALS_TRIAGE")

    gw.upsert_rows([_row("m1", 0, verdict="Pass")])
    gw.upsert_rows([_row("m1", 0, verdict="Reject")])  # same key, new verdict

    data = _data_rows(service)
    assert len(data) == 1  # no duplicate
    assert data[0][HEADER.index("verdict")] == "Reject"  # updated in place


def test_distinct_keys_each_append():
    service = _FakeSheetsService()
    gw = SheetsGateway(service, "sid", "DEALS_TRIAGE")

    gw.upsert_rows([_row("m1", 0), _row("m1", 1), _row("m2", 0)])

    assert len(_data_rows(service)) == 3
