"""Calculator Gateway upsert against a stateful fake Sheets API.

Asserts the external behavior against the DEALS_APP schema: a new id appends, an
existing id updates in place (idempotent feed — no duplicate on re-run), and the
written row matches the Calculator's column contract.
"""

import json
import re

from dealdesk.calculator_gateway import DEALS_APP_HEADER, CalculatorGateway


class _FakeValues:
    """In-memory model of the DEALS_APP tab: a list of full rows."""

    def __init__(self, store):
        self._store = store

    def get(self, spreadsheetId, range):
        # Column A only (id key), mirroring the real findRowById request.
        rows = [[r[0]] if r else [] for r in self._store["rows"]]
        return _Exec({"values": rows})

    def update(self, spreadsheetId, range, valueInputOption, body):
        rownum = _row_of(range)
        while len(self._store["rows"]) < rownum:
            self._store["rows"].append([])
        self._store["rows"][rownum - 1] = body["values"][0]
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
    def __init__(self, seed_rows=None):
        self.store = {"rows": list(seed_rows or [])}
        self._spreadsheets = _FakeSpreadsheets(self.store)

    def spreadsheets(self):
        return self._spreadsheets


def _row_of(rng: str) -> int:
    return int(re.search(r"![A-Z]+(\d+)", rng).group(1))


def _deal(price=250000, arv=0, address="9 Oak Dr"):
    return {
        "purchasePrice": price,
        "arv": arv,
        "monthlyRent": 2000,
        "hmlLevPP": 69.565,
        "refiLtv": 65,
        "address": address,
    }


def test_new_id_appends_a_row():
    # Seed a header row as the Calculator would have created it.
    service = _FakeSheetsService(seed_rows=[list(DEALS_APP_HEADER)])
    gw = CalculatorGateway(service, "sid", "DEALS_APP")

    gw.upsert_deal("dd-m1-0", _deal())

    data = [r for r in service.store["rows"] if r and r[0] != DEALS_APP_HEADER[0]]
    assert len(data) == 1
    assert data[0][0] == "dd-m1-0"


def test_row_matches_deals_app_schema():
    service = _FakeSheetsService(seed_rows=[list(DEALS_APP_HEADER)])
    gw = CalculatorGateway(service, "sid", "DEALS_APP")

    gw.upsert_deal("dd-m1-0", _deal(arv=0, address="9 Oak Dr"))

    row = service.store["rows"][1]
    assert len(row) == len(DEALS_APP_HEADER)
    assert row[DEALS_APP_HEADER.index("id")] == "dd-m1-0"
    assert row[DEALS_APP_HEADER.index("address")] == "9 Oak Dr"
    assert row[DEALS_APP_HEADER.index("arv")] == "0"  # summary mirrors the sentinel
    # Computed summary columns are left blank for the Calculator to fill on open.
    assert row[DEALS_APP_HEADER.index("score")] == ""
    assert row[DEALS_APP_HEADER.index("moneyInDeal")] == ""
    assert row[DEALS_APP_HEADER.index("monthlyNOI")] == ""
    assert row[DEALS_APP_HEADER.index("settingsJson")] == ""
    inputs = json.loads(row[DEALS_APP_HEADER.index("inputsJson")])
    assert inputs["purchasePrice"] == 250000
    assert inputs["hmlLevPP"] == 69.565


def test_same_id_updates_in_place_no_duplicate():
    service = _FakeSheetsService(seed_rows=[list(DEALS_APP_HEADER)])
    gw = CalculatorGateway(service, "sid", "DEALS_APP")

    gw.upsert_deal("dd-m1-0", _deal(price=250000))
    gw.upsert_deal("dd-m1-0", _deal(price=240000))  # re-run: price dropped

    data = [r for r in service.store["rows"] if r and r[0] != DEALS_APP_HEADER[0]]
    assert len(data) == 1  # idempotent — no duplicate
    inputs = json.loads(data[0][DEALS_APP_HEADER.index("inputsJson")])
    assert inputs["purchasePrice"] == 240000  # updated in place


def test_distinct_ids_each_append():
    service = _FakeSheetsService(seed_rows=[list(DEALS_APP_HEADER)])
    gw = CalculatorGateway(service, "sid", "DEALS_APP")

    gw.upsert_deal("dd-m1-0", _deal())
    gw.upsert_deal("dd-m1-1", _deal())
    gw.upsert_deal("dd-m2-0", _deal())

    data = [r for r in service.store["rows"] if r and r[0] != DEALS_APP_HEADER[0]]
    assert len(data) == 3
