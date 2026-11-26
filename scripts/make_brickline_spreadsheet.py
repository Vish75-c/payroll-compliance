"""Build data/brickline/brickline_hr_data.xlsx from data/brickline/seed.json.

The spreadsheet deliberately uses different column names from the demo HR tool
("Employee ID" vs "Emp Code", "Basic" vs "Basic Salary") to show that both ways in
end up in the same shape of data.
"""
import json
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parent.parent
seed = json.loads((ROOT / "data/brickline/seed.json").read_text())

wb = Workbook()
ws = wb.active
ws.title = "Employees"
ws.append(["Employee ID", "Employee Name", "Gender", "Work Location", "Date of Joining", "Date of Leaving", "Department", "Designation"])
for e in seed["employees"]:
    ws.append([e["code"], e["name"], {"F": "Female", "M": "Male"}[e["gender"]], e["location"], date.fromisoformat(e["doj"]),
               date.fromisoformat(e["doe"]) if e.get("doe") else None, e["department"], e["designation"]])

ws = wb.create_sheet("Salary")
ws.append(["Employee ID", "Effective From", "Basic", "HRA", "Special Allowance", "Monthly Gross", "Recorded On"])
for s in seed["salaries"]:
    ws.append([s["code"], date.fromisoformat(s["effective_from"]), s["basic"], s["hra"], s["special"],
               s["basic"] + s["hra"] + s["special"], date.fromisoformat(s["recorded_on"])])

ws = wb.create_sheet("Leave")
ws.append(["Employee ID", "Leave Type", "From", "To", "Days", "Status"])
for lv in seed["leave"]:
    ws.append([lv["code"], lv["type"], date.fromisoformat(lv["from"]), date.fromisoformat(lv["to"]), lv["days"], lv["status"]])

for sheet in wb.worksheets:
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E8EEF7")
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = max(14, max(len(str(c.value or "")) for c in column) + 2)
        for c in column[1:]:
            if isinstance(c.value, date):
                c.number_format = "DD-MMM-YYYY"

out = ROOT / "data/brickline/brickline_hr_data.xlsx"
wb.save(out)
print(f"wrote {out}")

# The same data as three separate files, the way many companies keep it (see "Upload spreadsheets").
import csv  # noqa: E402

separate = ROOT / "data/brickline/separate_files"
separate.mkdir(exist_ok=True)
for sheet, name in (("Employees", "employee_master.xlsx"), ("Salary", "salary_contracts.xlsx")):
    one = Workbook()
    for row in wb[sheet].iter_rows(values_only=True):
        one.active.append(row)
    one.save(separate / name)
with open(separate / "leave_july.csv", "w", newline="") as f:
    csv.writer(f).writerows([[v.isoformat() if isinstance(v, date) else v for v in row] for row in wb["Leave"].iter_rows(values_only=True)])
print(f"wrote {separate}/ (employee_master.xlsx, salary_contracts.xlsx, leave_july.csv)")


# Daily attendance for August-October, one file per month, as an attendance or punch report would give it: a row for
# every working day someone checked in. The same days and times Horilla's seed enters (integrations/horilla/seed_brickline.py).
import random  # noqa: E402
from datetime import timedelta  # noqa: E402

holidays = {date.fromisoformat(d) for _, d in seed["holidays"]}
gaps = {(g["code"], g["date"]) for g in seed["attendance_gaps"]}
no_card = {(x["code"], x["month"]) for x in seed["no_attendance"]}
on_leave = {(lv["code"], (date.fromisoformat(lv["from"]) + timedelta(k)).isoformat())
            for lv in seed["leave"] if lv["status"] == "Approved"
            for k in range((date.fromisoformat(lv["to"]) - date.fromisoformat(lv["from"])).days + 1)}
folder = ROOT / "data/brickline/attendance"
folder.mkdir(exist_ok=True)
for month in ("2025-08", "2025-09", "2025-10"):
    y, m = map(int, month.split("-"))
    first = date(y, m, 1)
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
    rows = []
    for e in seed["employees"]:
        start = max(first, date.fromisoformat(e["doj"]))
        end = min(last, date.fromisoformat(e["doe"])) if e.get("doe") else last
        day = start
        while day <= end:
            key = (e["code"], day.isoformat())
            if (day.weekday() < 5 and day not in holidays and key not in on_leave and key not in gaps
                    and (e["code"], month) not in no_card):
                rng = random.Random(f"{e['code']}{day}")  # same times as Horilla's seed
                clock_in = 9 * 60 + rng.randint(0, 45)
                worked = 8 * 60 + rng.randint(40, 85)
                rows.append([e["code"], day.isoformat(), "%02d:%02d" % divmod(clock_in, 60), "%02d:%02d" % divmod(clock_in + worked, 60)])
            day += timedelta(days=1)
    with open(folder / f"attendance_{month}.csv", "w", newline="") as f:
        csv.writer(f).writerows([["Employee ID", "Date", "Check-in", "Check-out"]] + sorted(rows, key=lambda r: (r[1], r[0])))
print(f"wrote {folder}/ (attendance_2025-08.csv, attendance_2025-09.csv, attendance_2025-10.csv)")
