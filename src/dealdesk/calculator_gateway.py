"""Calculator Gateway — thin I/O adapter over the Calculator's ``DEALS_APP`` tab.

DealDesk's downstream sink (ADR-0002): write a **partial Deal** row so the
Calculator has results waiting when the operator opens it. Write-only this phase
— DealDesk never reads results back.

Idempotent **by row id** (column A): a row whose id already exists is *updated in
place*; a new id is appended. Because the id is derived deterministically from the
Triage key (see ``deal_input.feed_row_id``), a crash-and-retry updates the same
row rather than duplicating the deal.

The ``DEALS_APP`` schema is owned by ``brrrr-calculator`` (``lib/sheets.ts``);
this module must match it. DealDesk fills only ``id``, ``address``, ``savedAt``,
``arv`` (summary), ``inputsJson``; the computed summary columns (``score``,
``moneyInDeal``, ``monthlyNOI``) are left blank for the Calculator to populate on
open, and ``settingsJson`` is empty (no Term Sheet).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

# Must mirror brrrr-calculator/lib/sheets.ts HEADERS exactly.
DEALS_APP_HEADER = (
    "id",
    "address",
    "savedAt",
    "score",
    "arv",
    "moneyInDeal",
    "monthlyNOI",
    "inputsJson",
    "settingsJson",
)

_NUM_COLS = len(DEALS_APP_HEADER)
_LAST_COL = chr(ord("A") + _NUM_COLS - 1)  # "I"


def build_deals_app_values(row_id: str, deal_input: dict, saved_at: str) -> list[str]:
    """The full ``DEALS_APP`` row for a fed Deal (pure). ``score``/``moneyInDeal``/
    ``monthlyNOI`` are left blank — the Calculator computes them live on open. The
    ``arv`` summary column mirrors the partial Deal's ARV (``0`` when unknown)."""
    return [
        row_id,
        str(deal_input.get("address", "")),
        saved_at,
        "",  # score — computed on open
        str(deal_input.get("arv", 0)),
        "",  # moneyInDeal — computed on open
        "",  # monthlyNOI — computed on open
        json.dumps(deal_input, sort_keys=True),
        "",  # settingsJson — no Term Sheet
    ]


class CalculatorGateway:
    """Wraps an authorized Sheets service. Inject a fake in tests."""

    def __init__(self, service, spreadsheet_id: str, tab: str = "DEALS_APP"):
        self._service = service
        self._spreadsheet_id = spreadsheet_id
        self._tab = tab

    def upsert_deal(self, row_id: str, deal_input: dict) -> str:
        """Write (or update) the ``DEALS_APP`` row for ``row_id``. Returns the id
        so the caller can record it on the Triage Log row (the triage→calc link)."""
        values = self._service.spreadsheets().values()
        rownum = self._find_row_by_id(values, row_id)
        row = build_deals_app_values(row_id, deal_input, _now_iso())
        if rownum is None:
            self._append(values, row)
        else:
            self._update(values, rownum, row)
        return row_id

    def _find_row_by_id(self, values, row_id: str) -> int | None:
        resp = values.get(
            spreadsheetId=self._spreadsheet_id,
            range=f"{self._tab}!A:A",
        ).execute()
        grid = resp.get("values", [])
        for i, cells in enumerate(grid):
            if cells and cells[0] == row_id:
                return i + 1  # sheets rows are 1-based
        return None

    def _update(self, values, rownum: int, row: list[str]) -> None:
        values.update(
            spreadsheetId=self._spreadsheet_id,
            range=f"{self._tab}!A{rownum}:{_LAST_COL}{rownum}",
            valueInputOption="RAW",
            body={"values": [row]},
        ).execute()

    def _append(self, values, row: list[str]) -> None:
        values.append(
            spreadsheetId=self._spreadsheet_id,
            range=f"{self._tab}!A:{_LAST_COL}",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
