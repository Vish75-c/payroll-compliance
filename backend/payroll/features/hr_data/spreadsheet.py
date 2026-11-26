"""Read a spreadsheet upload (xlsx or csv) into raw tables, one per sheet."""
from __future__ import annotations

import csv
import io
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
    return {"name": name, "headers": headers, "rows": rows, "read_info": {"method": "spreadsheet", "header_row": best_index + 1}}


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
