"""Sheets Gateway — thin I/O adapter over the ``DEALS_TRIAGE`` sheet.

Phase 2 exposes one capability: idempotently upsert Triage Log rows, keyed on
(message-id, property index). A row whose key already exists is *updated in
place*; a new key is appended. This is the idempotency backstop that lets a
crash/retry re-process an Email without duplicating its rows.
"""

from __future__ import annotations

from .triage_log import HEADER, TriageRow

_KEY_RANGE_COLS = "A:B"  # message_id, property_index
_NUM_COLS = len(HEADER)
_LAST_COL = chr(ord("A") + _NUM_COLS - 1)  # inclusive last column letter


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
