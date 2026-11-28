"""Read a spreadsheet upload (xlsx or csv) into raw tables, one per sheet."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal

from openpyxl import load_workbook


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return str(Decimal(repr(value)).normalize()) if value != int(value) else str(int(value))
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value).strip()


def _to_table(name: str, grid: list[list[str]]) -> dict | None:
    grid = [row for row in grid]
    if not grid:
        return None
    # The header is the row (among the first ten) with the most filled, non-numeric cells.
    best_index, best_score = None, 0
    for i, row in enumerate(grid[:10]):
        score = sum(1 for v in row if v and not _looks_numeric(v))
        if score > best_score:
            best_index, best_score = i, score
    if best_index is None or best_score < 2:
        return None
    raw_headers = grid[best_index]
    width = max(i + 1 for i, v in enumerate(raw_headers) if v) if any(raw_headers) else 0
    headers, seen = [], {}
    for i in range(width):
        header = raw_headers[i] if i < len(raw_headers) and raw_headers[i] else f"Column {i + 1}"
        if header in seen:
            seen[header] += 1
            header = f"{header} ({seen[header]})"
        else:
            seen[header] = 1
        headers.append(header)
    rows = []
    for row in grid[best_index + 1:]:
        cells = [(row[i] if i < len(row) else "") for i in range(width)]
        if not any(cells):
            continue
        rows.append(dict(zip(headers, cells)))
    notes = []
    while rows and _is_note(rows[-1]):
        notes.insert(0, " ".join(v for v in rows.pop().values() if v))
    info = {"method": "spreadsheet", "header_row": best_index + 1}
    if notes:
        info["notes_skipped"] = notes
    return {"name": name, "headers": headers, "rows": rows, "read_info": info}


def _is_note(row: dict) -> bool:
    """A line of text under the data, such as an export template's "Notes: Columns in red are mandatory", not a person.

    Only rows at the end of the sheet are looked at, and only if every filled cell reads as a sentence or a heading
    ("Notes:"): an employee ID, an amount or a date anywhere in the row makes it data."""
    filled = [v for v in row.values() if v]
    if not filled:
        return False
    for value in filled:
        if _looks_numeric(value) or _looks_like_date(value) or _looks_like_code(value):
            return False
        if not (value.endswith(":") or len(value.split()) >= 3):
            return False
    return True


DATE_LIKE = re.compile(r"^\d{1,4}[-/. ](\d{1,2}|[A-Za-z]{3,9})[-/. ]\d{1,4}$")
CODE_LIKE = re.compile(r"^(?=.*\d)[A-Za-z0-9/_.-]{2,}$")  # E0001, BL-016, MH/BAN/123


def _looks_like_date(text: str) -> bool:
    return bool(DATE_LIKE.match(text.strip()))


def _looks_like_code(text: str) -> bool:
    return bool(CODE_LIKE.match(text.strip()))


def _looks_numeric(text: str) -> bool:
    try:
        float(text.replace(",", ""))
        return True
    except ValueError:
        return False


def read_spreadsheet(filename: str, content: bytes) -> list[dict]:
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        workbook = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        tables = []
        for sheet in workbook.worksheets:
            # Some exports declare a wrong sheet size (e.g. "A1" for a whole table). Excel ignores it; the fast reader
            # trusts it and would see one cell. Measure the sheet from its rows instead.
            if hasattr(sheet, "reset_dimensions"):
                sheet.reset_dimensions()
            grid = [[_cell_text(v) for v in row] for row in sheet.iter_rows(values_only=True)]
            table = _to_table(sheet.title, grid)
            if table and table["rows"]:
                tables.append(table)
        workbook.close()
        return tables
    if lower.endswith((".csv", ".txt")):
        text = content.decode("utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|") if text.strip() else csv.excel
        grid = [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]
        table = _to_table(filename.rsplit("/", 1)[-1].rsplit(".", 1)[0], grid)
        return [table] if table and table["rows"] else []
    raise ValueError("Please upload an .xlsx or .csv file")
