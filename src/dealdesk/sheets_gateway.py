"""Sheets Gateway — thin I/O adapter over the ``DEALS_TRIAGE`` sheet.

The core capability is an idempotent upsert of Triage Log rows, keyed on
(message-id, property index). A row whose key already exists is *updated in
place*; a new key is appended. This is the idempotency backstop that lets a
crash/retry re-process an Email without duplicating its rows.

Two read paths serve the guards built on top of it: ``fetch_notified`` (the
notification guard, Phase 4) and ``fetch_prior_properties`` (the re-send lookup,
Phase 6). Both are strictly read-only — in particular the re-send scan never
rewrites or re-evaluates the earlier rows it reads.
"""

from __future__ import annotations

import json

from .resend import PriorProperty
from .triage_log import HEADER, TriageRow

_KEY_RANGE_COLS = "A:B"  # message_id, property_index
_NUM_COLS = len(HEADER)
_LAST_COL = chr(ord("A") + _NUM_COLS - 1)  # inclusive last column letter
_NOTIFIED_COL_INDEX = HEADER.index("notified")  # 0-based column of the notified flag
_ADDRESS_COL_INDEX = HEADER.index("address")
_VERDICT_COL_INDEX = HEADER.index("verdict")
_FACTS_COL_INDEX = HEADER.index("facts_json")
_RECEIVED_COL_INDEX = HEADER.index("received_date")


class SheetsGateway:
    """Wraps an authorized Sheets service. Inject a fake in tests."""

    def __init__(self, service, spreadsheet_id: str, tab: str = "DEALS_TRIAGE"):
        self._service = service
        self._spreadsheet_id = spreadsheet_id
        self._tab = tab

    def upsert_rows(self, rows: list[TriageRow]) -> None:
        if not rows:
            return
        values = self._service.spreadsheets().values()
        key_to_rownum = self._read_key_index(values)

        for row in rows:
            rownum = key_to_rownum.get(row.key)
            if rownum is None:
                self._append(values, row)
                # Newly appended rows land after the current max row; track them
                # so two rows with the same key in one batch don't both append.
                key_to_rownum[row.key] = self._next_rownum(key_to_rownum)
            else:
                self._update(values, rownum, row)

    def fetch_notified(self, message_id: str) -> set[int]:
        """Return the property indices of ``message_id`` already marked
        ``notified`` in the sheet. The orchestrator reads this before sending so a
        retry never re-emails a Property (the ``notified`` guard). Reads the full
        row range once and filters to the Email in memory."""
        notified: set[int] = set()
        for cells in self._read_grid():
            if not cells or cells[0] != message_id:
                continue
            if len(cells) <= _NOTIFIED_COL_INDEX:
                continue
            if str(cells[_NOTIFIED_COL_INDEX]).strip().upper() == "TRUE":
                try:
                    notified.add(int(cells[1]))
                except (ValueError, IndexError):
                    continue
        return notified

    def fetch_prior_properties(self) -> list[PriorProperty]:
        """Every Property already recorded in the Triage Log, in sheet order —
        the re-send lookup's source. One read per run; the caller indexes them by
        normalized address. Read-only: the rows scanned here are never rewritten
        or re-evaluated."""
        priors: list[PriorProperty] = []
        for cells in self._read_grid():
            if not cells or cells[0] == HEADER[0]:
                continue  # header row
            index = _int_at(cells, 1)
            if index is None:
                continue  # no Triage key -> not a Property row we can trust
            priors.append(
                PriorProperty(
                    message_id=cells[0],
                    property_index=index,
                    address=_at(cells, _ADDRESS_COL_INDEX),
                    verdict=_at(cells, _VERDICT_COL_INDEX),
                    price=_price_of(_at(cells, _FACTS_COL_INDEX)),
                    received_date=_at(cells, _RECEIVED_COL_INDEX),
                )
            )
        return priors

    def _read_grid(self) -> list[list[str]]:
        resp = self._service.spreadsheets().values().get(
            spreadsheetId=self._spreadsheet_id,
            range=f"{self._tab}!A:{_LAST_COL}",
        ).execute()
        return resp.get("values", [])

    def _read_key_index(self, values) -> dict[tuple[str, str], int]:
        resp = values.get(
            spreadsheetId=self._spreadsheet_id,
            range=f"{self._tab}!{_KEY_RANGE_COLS}",
        ).execute()
        grid = resp.get("values", [])
        if not grid:
            self._write_header(values)
            return {}

        index: dict[tuple[str, str], int] = {}
        for i, cells in enumerate(grid):
            rownum = i + 1  # sheets rows are 1-based
            if rownum == 1 and (not cells or cells[0] == HEADER[0]):
                continue  # header row
            if len(cells) >= 2:
                index[(cells[0], cells[1])] = rownum
        return index

    def _write_header(self, values) -> None:
        values.update(
            spreadsheetId=self._spreadsheet_id,
            range=f"{self._tab}!A1",
            valueInputOption="RAW",
            body={"values": [list(HEADER)]},
        ).execute()

    def _update(self, values, rownum: int, row: TriageRow) -> None:
        values.update(
            spreadsheetId=self._spreadsheet_id,
            range=f"{self._tab}!A{rownum}:{_LAST_COL}{rownum}",
            valueInputOption="RAW",
            body={"values": [row.to_values()]},
        ).execute()

    def _append(self, values, row: TriageRow) -> None:
        values.append(
            spreadsheetId=self._spreadsheet_id,
            range=f"{self._tab}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row.to_values()]},
        ).execute()

    @staticmethod
    def _next_rownum(index: dict[tuple[str, str], int]) -> int:
        return (max(index.values()) if index else 1) + 1


# Sheets truncates trailing empty cells, so a short row is normal, not corrupt.
def _at(cells: list[str], index: int) -> str:
    return str(cells[index]) if len(cells) > index else ""


def _int_at(cells: list[str], index: int) -> int | None:
    try:
        return int(_at(cells, index))
    except ValueError:
        return None


def _price_of(facts_json: str) -> float | None:
    """The purchase price out of a row's stored facts — the ``@ $300k`` half of
    the re-send breadcrumb. A row written by an older schema, or with unreadable
    facts, simply contributes no price."""
    if not facts_json:
        return None
    try:
        facts = json.loads(facts_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(facts, dict):
        return None
    value = facts.get("purchase_price")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
