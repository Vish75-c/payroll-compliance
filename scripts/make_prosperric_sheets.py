"""prosperric acid's HR data as Horilla export sheets, to upload in the payroll app (HR data → upload files).

    .venv/bin/python scripts/make_prosperric_sheets.py      ->  data/prosperric/

The same columns as Horilla's own exports (integrations/horilla/fixtures/), so the app's Horilla profile maps every
column without asking: employee_export.xlsx (Employees), Contract_export.xlsx (salaries), Leave_requests.xlsx (with
Horilla's Start/End Date Breakdown for half days) and Attendance_export_2026-11.xlsx (a check-in per working day).

The people are the Kestrel sample (data/kestrel/seed.json, the people prosperric acid already pays). On top, what
HR enters for December 2026, the month after November was paid:

  * KS006 Rohan Iyer joins Engineering on 7 Dec (₹55,000): the Shift staff group takes him by its department rule.
  * KS002 Meera Joshi: half a day unpaid on 15 Dec (Second Half), 0.5 day.
  * KS003 Sameer Kulkarni: unpaid 29-30 Dec, after the 25th: on a 26th-to-25th count it falls in January's pay.
  * KS001 Arvind Rao: unpaid on 27 Nov. November counted unpaid days up to 25 Nov, so December counts it.
  * KS005 Anjali Desai: sick leave on 10 Dec, paid: no cut.
Deterministic: running it again gives the same files.
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/prosperric"
SEED = json.loads((ROOT / "data/kestrel/seed.json").read_text())
COMPANY = "prosperric acid"

DECEMBER_PEOPLE = [{"code": "KS006", "name": "Rohan Iyer", "gender": "M", "location": "Bengaluru, Karnataka", "doj": "2026-12-07",
                    "department": "Engineering", "designation": "Data Engineer"}]
DECEMBER_SALARIES = [{"code": "KS006", "effective_from": "2026-12-07", "basic": 27500, "hra": 11000, "special_allowance": 16500}]
DECEMBER_LEAVE = [
    {"code": "KS002", "type": "Loss of Pay", "from": "2026-12-15", "start_breakdown": "Second Half", "to": "2026-12-15",
     "end_breakdown": "Second Half", "days": 0.5, "status": "Approved"},
    {"code": "KS003", "type": "Loss of Pay", "from": "2026-12-29", "start_breakdown": "Full Day", "to": "2026-12-30",
     "end_breakdown": "Full Day", "days": 2, "status": "Approved"},
    {"code": "KS001", "type": "Loss of Pay", "from": "2026-11-27", "start_breakdown": "Full Day", "to": "2026-11-27",
     "end_breakdown": "Full Day", "days": 1, "status": "Approved"},
    {"code": "KS005", "type": "Sick Leave", "from": "2026-12-10", "start_breakdown": "Full Day", "to": "2026-12-10",
     "end_breakdown": "Full Day", "days": 1, "status": "Approved"},
]
HOLIDAYS = {date(2026, 11, 1)}  # Kannada Rajyotsava (a Sunday in 2026)
ATTENDANCE = (date(2026, 11, 2), date(2026, 11, 25))

people = SEED["employees"] + DECEMBER_PEOPLE
salaries = SEED["salaries"] + DECEMBER_SALARIES
leave = SEED["leave"] + DECEMBER_LEAVE
names = {e["code"]: f"{e['name']} ({e['code']})" for e in people}  # how Horilla writes an employee in its exports


def save(headers: list[str], rows: list[list], name: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(headers)
    for row in rows:
        ws.append(row)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E7F0E9")
    for column in ws.columns:
        ws.column_dimensions[column[0].column_letter].width = max(12, max(len(str(c.value or "")) for c in column) + 2)
    wb.save(OUT / name)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    by_code = {e["code"]: e for e in people}

    save(["Badge ID", "First Name", "Last Name", "Email", "Phone", "Gender", "Department", "Job Position", "Job Role", "Shift",
          "Work Type", "Reporting Manager", "Employee Type", "Location", "Date Joining", "Basic Salary", "Salary Hour",
          "Contract End Date", "Company"],
         [[e["code"], *e["name"].split(" ", 1), f"{e['name'].lower().replace(' ', '.')}@prosperric.example", f"98450{int(e['code'][2:]):05d}",
           {"F": "female", "M": "male"}[e["gender"]], e["department"], e["designation"], e["designation"], "General (09:30-18:30)",
           "Work from office", "" if e["code"] == "KS001" else by_code["KS001"]["name"], "Full-time", e["location"], e["doj"], "0", "0",
           None, COMPANY] for e in people],
         "employee_export.xlsx")

    contracts, seen = [], {}
    ordered = sorted(salaries, key=lambda r: (r["code"], r["effective_from"]))
    for i, s in enumerate(ordered):
        nxt = ordered[i + 1] if i + 1 < len(ordered) and ordered[i + 1]["code"] == s["code"] else None
        end = (date.fromisoformat(nxt["effective_from"]) - timedelta(days=1)).isoformat() if nxt else None
        start = date.fromisoformat(s["effective_from"])
        label = f"{by_code[s['code']]['name']} {'revised ' if s['code'] in seen else ''}{start:%b %Y}"
        seen[s["code"]] = True
        contracts.append([label, names[s["code"]], s["effective_from"], end, "Monthly", s["basic"] + s["hra"] + s["special_allowance"],
                          None, "Expired" if end else "Active"])
    save(["Contract", "Employee", "Start Date", "End Date", "Wage Type", "Basic Salary", "Filing Status", "Status"], contracts,
         "Contract_export.xlsx")

    save(["Employee", "Start Date", "Start Date Breakdown", "End Date", "End Date Breakdown", "Requested Days", "Description",
          "Status", "Leave Type"],
         [[names[lv["code"]], lv["from"], lv.get("start_breakdown", "Full Day"), lv["to"], lv.get("end_breakdown", "Full Day"),
           lv["days"], "prosperric acid demo data", lv["status"], lv["type"]] for lv in sorted(leave, key=lambda r: (r["from"], r["code"]))],
         "Leave_requests.xlsx")

    on_leave = set()
    for lv in leave:
        if lv["status"] == "Approved":
            day = date.fromisoformat(lv["from"])
            while day <= date.fromisoformat(lv["to"]):
                on_leave.add((lv["code"], day))
                day += timedelta(days=1)
    rows = []
    day = ATTENDANCE[0]
    while day <= ATTENDANCE[1]:
        for e in people:
            if day.weekday() < 5 and day not in HOLIDAYS and (e["code"], day) not in on_leave and day >= date.fromisoformat(e["doj"]):
                rng = random.Random(f"{e['code']}{day}")
                start = 9 * 60 + rng.randint(0, 45)
                worked = 8 * 60 + rng.randint(40, 85)
                rows.append([names[e["code"]], "General (09:30-18:30)", "Work from office", day.isoformat(),
                             "%02d:%02d:00" % divmod(start, 60), day.isoformat(), "%02d:%02d:00" % divmod(start + worked, 60),
                             day.isoformat(), "%02d:%02d" % divmod(worked, 60), "Yes"])
        day += timedelta(days=1)
    save(["Employee", "Shift", "Work Type", "Attendance date", "Check-In", "Check-In Date", "Check-Out", "Check-Out Date",
          "Worked Hours", "Attendance Validate"], rows, "Attendance_export_2026-11.xlsx")
    print(f"wrote {OUT}/ ({len(people)} people, {len(contracts)} contracts, {len(leave)} leave requests, {len(rows)} attendance days)")


if __name__ == "__main__":
    main()
