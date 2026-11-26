# prosperric acid: HR data as Horilla export sheets

Made by `scripts/make_prosperric_sheets.py`. Same columns as Horilla's own exports, so the payroll app's Horilla
profile maps every column without asking.

| File | What it is |
|---|---|
| `employee_export.xlsx` | 6 people: KS001–KS005 (as paid in November) and KS006 Rohan Iyer, joining Engineering on 7 Dec |
| `Contract_export.xlsx` | Monthly salaries; KS002's raise to ₹1,40,000 from 1 Nov is a second contract |
| `Leave_requests.xlsx` | Leave with Horilla's Start/End Date Breakdown (First Half / Second Half = half a day) |
| `Attendance_export_2026-11.xlsx` | A check-in per working day, 2–25 Nov 2026 |

## Upload

In the app, with **prosperric acid** selected: **HR data → upload files**, add all four together, confirm the
columns, mark the data as right. Then open **December 2026** on Payroll months and calculate.

## What December shows (pay groups: Management, Shift staff from Dec; Everyone else)

| Who | Leave | December's unpaid days |
|---|---|---|
| KS001 Arvind (Management, calendar) | unpaid 27 Nov | **1**: November counted unpaid days only up to 25 Nov, so December picks it up |
| KS002 Meera (Shift staff, 26th–25th) | unpaid 15 Dec, Second Half | **0.5** |
| KS003 Sameer | unpaid 29–30 Dec | **0**: both his groups count unpaid days 26th–25th, so 29–30 Dec is cut in January's pay |
| KS005 Anjali | sick leave 10 Dec | **0**: paid leave |
| KS006 Rohan | joins 7 Dec, Engineering | goes into *Shift staff* by the department rule; paid from 7 Dec |
