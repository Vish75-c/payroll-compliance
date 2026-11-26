"""Build the sample company for the onboarding demo: Northwind Interiors Pvt Ltd, Mumbai.

Northwind moves to this system in October 2025, halfway through the financial year, so it also brings
the pay and TDS of April-September from its old payroll (opening balances). Its HR export uses other
column names than Brickline's ("Emp Code", "Sex", "With Effect From", ...) to show the mapping step.

Writes data/northwind/: seed.json (the facts, used by the tests), employees.xlsx, salaries.xlsx,
leave.csv and opening_balances.csv. Deterministic: running it again gives the same files.
"""
import csv
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/northwind"

# code, name, gender, city, joined, department, designation, monthly gross, basic share (None = 50/20/30)
PEOPLE = [
    ("NW001", "Farah Siddiqui", "F", "Mumbai", "2018-07-02", "Leadership", "Managing Director", 320000, None),
    ("NW002", "Rohan Mehta", "M", "Mumbai", "2019-01-14", "Projects", "Head of Projects", 240000, None),
    ("NW003", "Ishita Banerjee", "F", "Mumbai", "2019-09-02", "Design", "Principal Designer", 180000, Decimal("0.40")),
    ("NW004", "Kunal Deshpande", "M", "Pune", "2020-02-17", "Projects", "Site Head, Pune", 150000, None),
    ("NW005", "Anaya Kulkarni", "F", "Mumbai", "2020-06-08", "Design", "Senior Designer", 125000, None),
    ("NW006", "Vikram Shetty", "M", "Mumbai", "2020-11-23", "Finance", "Finance Manager", 110000, None),
    ("NW007", "Pooja Nair", "F", "Mumbai", "2021-03-01", "Design", "Designer", 95000, None),
    ("NW008", "Sameer Khan", "M", "Mumbai", "2021-05-17", "Procurement", "Procurement Lead", 85000, None),
    ("NW009", "Ritu Agarwal", "F", "Mumbai", "2021-08-09", "Projects", "Project Manager", 72000, None),
    ("NW010", "Aditya Joshi", "M", "Mumbai", "2021-12-06", "Projects", "Site Engineer", 65000, None),
    ("NW011", "Neha Patil", "F", "Pune", "2022-01-10", "Projects", "Site Engineer", 58000, None),
    ("NW012", "Harsh Vora", "M", "Mumbai", "2022-04-04", "Design", "3D Visualiser", 52000, None),
    ("NW013", "Sana Qureshi", "F", "Mumbai", "2022-07-18", "People", "HR Executive", 46000, None),
    ("NW014", "Manoj Pawar", "M", "Mumbai", "2022-10-03", "Procurement", "Buyer", 40000, None),
    ("NW015", "Divya Menon", "F", "Mumbai", "2023-01-09", "Finance", "Accountant", 36000, None),
    ("NW016", "Tushar Gaikwad", "M", "Mumbai", "2023-04-03", "Projects", "Site Supervisor", 32000, None),
    ("NW017", "Kavita Rane", "F", "Mumbai", "2023-07-10", "Design", "Junior Designer", 28000, None),
    ("NW018", "Imran Shaikh", "M", "Mumbai", "2023-10-16", "Projects", "Carpentry Supervisor", 26000, None),
    ("NW019", "Shruti Jadhav", "F", "Mumbai", "2024-01-08", "Front office", "Office Coordinator", 24000, None),
    ("NW020", "Ganesh More", "M", "Mumbai", "2024-03-11", "Projects", "Site Technician", 21000, None),
    ("NW021", "Rhea D'Souza", "F", "Mumbai", "2025-06-09", "Design", "Design Associate", 28000, Decimal("0.35715")),  # basic 10,000 of 28,000: Labour Codes add-back
    ("NW022", "Prakash Sawant", "M", "Mumbai", "2025-08-01", "Projects", "Site Technician", 19500, None),
    ("NW023", "Meghna Iyer", "F", "Mumbai", "2025-11-17", "Front office", "Receptionist", 15000, None),
    ("NW024", "Akash Thakur", "M", "Mumbai", "2026-01-05", "Projects", "Helper", 18000, None),
    ("NW025", "Sunil Kamble", "M", "Mumbai", "2024-09-02", "Front office", "Office Assistant (part-time)", 9800, None),
]

# Raises: code, effective from, new monthly gross, recorded on. NW005's raise is approved late (recorded in
# January for November), so January pays arrears for November and December. NW014's is on time.
RAISES = [("NW005", "2025-11-01", 140000, "2026-01-08"), ("NW014", "2025-12-01", 44000, "2025-11-25")]

# code, type, from, to, days, status
LEAVE = [
    ("NW007", "Loss of Pay", "2025-10-14", "2025-10-15", 2, "Approved"),
    ("NW010", "Sick Leave", "2025-10-20", "2025-10-21", 2, "Approved"),
    ("NW016", "Casual Leave", "2025-10-31", "2025-10-31", 1, "Approved"),
    ("NW025", "Loss of Pay", "2025-11-07", "2025-11-07", 0.5, "Approved"),
    ("NW013", "Loss of Pay", "2025-11-12", "2025-11-12", 1, "Rejected"),
    ("NW009", "Earned Leave", "2025-11-24", "2025-11-28", 5, "Approved"),
    ("NW012", "Loss of Pay", "2025-12-22", "2025-12-24", 3, "Approved"),
    ("NW017", "Casual Leave", "2025-12-26", "2025-12-26", 1, "Requested"),
    ("NW018", "Loss of Pay", "2026-01-29", "2026-02-03", 5, "Approved"),  # spans January and February (not Sunday 1 Feb)
    ("NW022", "Loss of Pay", "2026-02-10", "2026-02-10", 1, "Approved"),
    ("NW003", "Sick Leave", "2026-02-16", "2026-02-17", 2, "Approved"),
]


def split(gross: int, basic_share: Decimal | None) -> tuple[int, int, int]:
    if basic_share is None:
        return gross // 2, gross // 5, gross - gross // 2 - gross // 5
    basic = int((gross * basic_share).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    hra = gross // 5
    return basic, hra, gross - basic - hra


def new_regime_tax(taxable: Decimal) -> Decimal:
    """Tax for 2025-26 under the new regime, as the old payroll would have estimated it."""
    slabs = [(400000, 0), (800000, "0.05"), (1200000, "0.10"), (1600000, "0.15"), (2000000, "0.20"), (2400000, "0.25"), (None, "0.30")]
    tax, lower = Decimal(0), Decimal(0)
    for upper, rate in slabs:
        top = taxable if upper is None else min(taxable, Decimal(upper))
        if top > lower:
            tax += (top - lower) * Decimal(rate)
        if upper is None or taxable <= upper:
            break
        lower = Decimal(upper)
    if taxable <= 1200000:
        tax -= min(tax, Decimal(60000))
    elif tax > taxable - 1200000:
        tax = taxable - 1200000
    return (tax * Decimal("1.04")).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    employees, salaries, leave = [], [], []
    for code, name, gender, city, joined, dept, title, gross, share in PEOPLE:
        employees.append({"code": code, "name": name, "gender": gender, "location": f"{city}, Maharashtra", "doj": joined,
                          "department": dept, "designation": title})
        basic, hra, special = split(gross, share)
        start = max(joined, "2025-04-01")
        salaries.append({"code": code, "effective_from": start, "basic": basic, "hra": hra, "special": special,
                         "recorded_on": min(start, "2025-03-28") if start == "2025-04-01" else joined})
    for code, start, gross, recorded in RAISES:
        share = next(p[8] for p in PEOPLE if p[0] == code)
        basic, hra, special = split(gross, share)
        salaries.append({"code": code, "effective_from": start, "basic": basic, "hra": hra, "special": special, "recorded_on": recorded})
    for code, kind, start, end, days, status in LEAVE:
        leave.append({"code": code, "type": kind, "from": start, "to": end, "days": days, "status": status})
    seed = {"_comment": "Facts of the onboarding sample company; files in this folder are made from it by scripts/make_northwind_data.py.",
            "company": {"code": "northwind", "name": "Northwind Interiors Pvt Ltd", "city": "Mumbai", "state": "MH",
                        "pan": "AABCN4321K", "tan": "MUMN04321E", "pf_establishment_code": "MHBAN0054321000",
                        "esi_employer_code": "34000543210001001", "pay_day": 1, "first_month": "2025-10",
                        "settings": {"lop": {"divisor": "30", "day_rate_rounding": None}}},
            "employees": employees, "salaries": salaries, "leave": leave}
    (OUT / "seed.json").write_text(json.dumps(seed, indent=1, ensure_ascii=False) + "\n")

    def styled(wb):
        for cell in wb.active[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="E7F0E9")
        for column in wb.active.columns:
            wb.active.column_dimensions[column[0].column_letter].width = max(12, max(len(str(c.value or "")) for c in column) + 2)
            for c in column[1:]:
                if isinstance(c.value, date):
                    c.number_format = "DD-MM-YYYY"

    wb = Workbook()
    wb.active.title = "Staff"
    wb.active.append(["Emp Code", "Full Name", "Sex", "Location", "DOJ", "Dept", "Job Title"])
    for e in employees:
        wb.active.append([e["code"], e["name"], {"F": "Female", "M": "Male"}[e["gender"]], e["location"].split(",")[0],
                          date.fromisoformat(e["doj"]), e["department"], e["designation"]])
    styled(wb)
    wb.save(OUT / "employees.xlsx")

    wb = Workbook()
    wb.active.title = "Pay"
    wb.active.append(["Emp Code", "With Effect From", "Basic Pay", "House Rent Allowance", "Special Allowance", "Approved On"])
    for s in salaries:
        wb.active.append([s["code"], date.fromisoformat(s["effective_from"]), s["basic"], s["hra"], s["special"],
                          date.fromisoformat(s["recorded_on"])])
    styled(wb)
    wb.save(OUT / "salaries.xlsx")

    with open(OUT / "leave.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Emp Code", "Leave Category", "Leave From", "Leave To", "No of Days", "Approval Status"])
        for lv in leave:
            w.writerow([lv["code"], lv["type"], lv["from"], lv["to"], lv["days"], lv["status"]])

    # What the old payroll paid and deducted from April to September 2025 (no unpaid leave in those months).
    with open(OUT / "opening_balances.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Employee ID", "Gross paid this year", "TDS this year"])
        for code, _, _, _, joined, _, _, gross, _ in PEOPLE:
            doj = date.fromisoformat(joined)
            if doj >= date(2025, 10, 1):
                continue
            paid, months = Decimal(0), 0
            for m in range(4, 10):
                days = {4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30}[m]
                first, last = date(2025, m, 1), date(2025, m, days)
                if doj > last:
                    continue
                worked = (last - max(doj, first)).days + 1
                paid += (Decimal(gross) * worked / days).quantize(Decimal(1), rounding=ROUND_HALF_UP)
                months += 1
            annual = new_regime_tax(max(Decimal(0), Decimal(gross) * 12 - 75000))
            tds = (annual / 12 * months).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            w.writerow([code, paid, tds])
    print(f"wrote {OUT}/ (seed.json, employees.xlsx, salaries.xlsx, leave.csv, opening_balances.csv)")


if __name__ == "__main__":
    main()
