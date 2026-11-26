"""Sample data with people joining and leaving mid-month, and the expected numbers to check the app against.

    .venv/bin/python scripts/make_riverbend_sample.py      ->  data/riverbend/

Riverbend Interiors Pvt Ltd is made up. 80 people; the first rows of each sheet are the special cases (joining or
leaving mid-month, unpaid leave across a month end, raises mid-month, ESI and PT edges, TDS with and without opening
balances, attendance gaps), each explained in the Remarks column. The rest are ordinary months.

The expected numbers are worked out here, from the rules as written in data/rules/statutory_in.json and the company
defaults (one day's pay = monthly pay ÷ days in the month). Nothing is imported from payroll_engine, so the app is
checked against a second calculation, not itself. Two months, November and December 2026, with a made-up government
change for the demo: the PF wage ceiling goes from 15,000 to 21,000 from 1 December 2026.
"""
from __future__ import annotations

import calendar
import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "riverbend"
RULES = {(v["key"], v.get("jurisdiction", "")): v["payload"]
         for v in json.loads((ROOT / "data/rules/statutory_in.json").read_text())["versions"]}
MONTHS = ["2026-11", "2026-12"]
FY_START = date(2026, 4, 1)
NEW_PF_CEILING = Decimal(21000)   # the demo's government change, from 1 December 2026
NEW_PF_FROM = date(2026, 12, 1)
D = Decimal


def rupee(x) -> Decimal:
    return D(x).quantize(D(1), rounding=ROUND_HALF_UP)


def rupee_up(x) -> Decimal:
    return D(x).quantize(D(1), rounding=ROUND_CEILING)


# --------------------------------------------------------------------------- months

def label_month(period: str) -> tuple[int, int]:
    y, m = map(int, period.split("-"))
    return y, m


def month_dates(period: str) -> tuple[date, date]:
    y, m = label_month(period)
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def period_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def next_period(period: str) -> str:
    y, m = label_month(period)
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y:04d}-{m:02d}"


def days_between(a: date, b: date):
    while a <= b:
        yield a
        a += timedelta(days=1)


# --------------------------------------------------------------------------- the people

COMPONENTS = ["basic", "hra", "special_allowance"]


def split(gross: int) -> dict[str, int]:
    return {"basic": gross * 50 // 100, "hra": gross * 20 // 100, "special_allowance": gross - gross * 50 // 100 - gross * 20 // 100}


@dataclass
class Person:
    code: str
    name: str
    gender: str
    city: str
    joined: date
    left: date | None = None
    department: str = "Design"
    designation: str = "Designer"
    salary: list[tuple[date, int, date]] = field(default_factory=list)   # (effective from, monthly gross, recorded on)
    leave: list[tuple[str, date, date, str]] = field(default_factory=list)  # (type, from, to, status)
    missed_punch: list[date] = field(default_factory=list)  # working days with no check-in and no leave
    opening_given: bool = True   # whether opening_balances.csv has this person's pay and TDS from April
    opening: tuple[Decimal, Decimal] | None = None   # worked out: pay and TDS of April to October
    remarks: str = ""
    look_for: str = ""

    @property
    def state(self) -> str:
        return "MH" if "Maharashtra" in self.city else "KA"


BLR, BOM = "Bengaluru, Karnataka", "Mumbai, Maharashtra"
APR1 = date(2026, 4, 1)
REC = date(2026, 3, 20)


def special_cases() -> list[Person]:
    P = Person
    return [
        P("RB001", "Kiran Rao", "Male", BLR, date(2026, 11, 10), salary=[(date(2026, 11, 10), 40000, date(2026, 11, 3))],
          remarks="Joins 10 Nov 2026 (mid-month)",
          look_for="1 to 9 Nov are before joining: paid 21 of 30 days = {RB001_nov_gross}. TDS counts this part month plus the full "
                   "salary for December to March. December: full ₹40,000."),
        P("RB002", "Sneha Kulkarni", "Female", BLR, date(2026, 11, 20), salary=[(date(2026, 11, 20), 30000, date(2026, 11, 10))],
          remarks="Joins 20 Nov 2026 (late in the month)",
          look_for="Paid 11 of 30 days = {RB002_nov_gross}. That is under ₹21,000, so ESI is taken for November although her salary "
                   "is ₹30,000 (ESI setting 'gross pay this month'; 'full monthly salary' avoids it). December: full month at ₹30,000, and ESI continues because she was covered in November (the October-March contribution period)."),
        P("RB003", "Farhan Ali", "Male", BLR, date(2026, 12, 1), salary=[(date(2026, 12, 1), 50000, date(2026, 11, 20))],
          remarks="Joins 1 Dec 2026",
          look_for="Not in November at all (joins after it ends). December: full ₹50,000."),
        P("RB004", "Lakshmi Menon", "Female", BLR, date(2022, 7, 4), left=date(2026, 11, 20), salary=[(APR1, 120000, REC)],
          remarks="Last working day 20 Nov 2026 (leaves mid-month)",
          look_for="21 to 30 Nov are after leaving: paid 20 of 30 days = {RB004_nov_gross}. November is her last month (months left = 1): "
                   "her pay for the year ends at {RB004_projected}, tax for the year {RB004_annual}, {RB004_paid} already deducted "
                   "April to October, so November TDS is {RB004_nov_tds}. Not in December."),
        P("RB005", "Rohan Desai", "Male", BLR, date(2023, 1, 9), left=date(2026, 11, 30), salary=[(APR1, 45000, REC)],
          remarks="Last working day 30 Nov 2026 (the month end)",
          look_for="November: full ₹45,000. Not in December."),
        P("RB006", "Ananya Bose", "Female", BLR, date(2021, 3, 15), salary=[(APR1, 50000, REC)],
          leave=[("Leave Without Pay", date(2026, 11, 28), date(2026, 12, 2), "Approved")],
          remarks="Unpaid leave 28 Nov to 2 Dec (5 days, across the month end)",
          look_for="Split by date: 3 unpaid days in November (÷ 30), 2 in December (÷ 31)."),
        P("RB007", "Vikram Singh", "Male", BLR, date(2020, 6, 1), salary=[(APR1, 62000, REC)],
          leave=[("Leave Without Pay", date(2026, 11, 12), date(2026, 11, 14), "Approved"),
                 ("Leave Without Pay", date(2026, 12, 15), date(2026, 12, 16), "Approved")],
          remarks="Unpaid leave 12 to 14 Nov (3 days) and 15 to 16 Dec (2 days)",
          look_for="November: 3 unpaid days ÷ 30. December: 2 unpaid days ÷ 31."),
        P("RB008", "Pooja Hegde", "Female", BLR, date(2024, 2, 12), salary=[(APR1, 38000, REC)],
          leave=[("Casual Leave", date(2026, 11, 5), date(2026, 11, 6), "Approved")],
          remarks="Casual leave 5 to 6 Nov (paid leave)",
          look_for="No pay cut, and the two days without check-in are not suggested as absences."),
        P("RB009", "Arvind Nair", "Male", BLR, date(2023, 9, 18), salary=[(APR1, 42000, REC)],
          leave=[("Leave Without Pay", date(2026, 11, 17), date(2026, 11, 18), "Rejected")],
          remarks="Unpaid leave request 17 to 18 Nov, Rejected",
          look_for="Rejected requests do not count: no cut."),
        P("RB010", "Divya Pillai", "Female", BLR, date(2022, 11, 7), salary=[(APR1, 40000, REC), (date(2026, 11, 10), 50000, date(2026, 11, 5))],
          remarks="Raise ₹40,000 -> ₹50,000 from 10 Nov (mid-month)",
          look_for="November weighted by days: 9 days at ₹40,000 + 21 days at ₹50,000, of 30 = {RB010_nov_gross}. December: ₹50,000."),
        P("RB011", "Sanjay Gupta", "Male", BLR, date(2021, 8, 23), salary=[(APR1, 60000, REC), (date(2026, 12, 1), 66000, date(2026, 11, 18))],
          remarks="Raise ₹60,000 -> ₹66,000 from 1 Dec",
          look_for="November: ₹60,000. December: ₹66,000."),
        P("RB012", "Meena Kumari", "Female", BLR, date(2024, 6, 3), department="Studio Ops", designation="Studio Assistant",
          salary=[(APR1, 18000, REC)],
          remarks="Low salary ₹18,000: ESI applies, no PT", look_for="PF ₹1,080, ESI ₹135 (company ₹585), PT ₹0."),
        P("RB013", "Suresh Babu", "Male", BLR, date(2024, 1, 8), department="Studio Ops", designation="Site Supervisor",
          salary=[(APR1, 21000, REC)],
          remarks="Salary exactly ₹21,000: the ESI limit", look_for="ESI applies at ₹21,000 or less: ₹158 (0.75% rounded up), company ₹683."),
        P("RB014", "Rekha Iyer", "Female", BLR, date(2024, 4, 15), department="Studio Ops", designation="Coordinator",
          salary=[(APR1, 24000, REC)],
          remarks="Salary ₹24,000: above ESI, below Karnataka PT", look_for="No ESI, PT ₹0 (Karnataka starts at ₹25,000)."),
        P("RB015", "Manoj Patil", "Male", BLR, date(2023, 5, 22), department="Studio Ops", designation="Coordinator",
          salary=[(APR1, 25000, REC)],
          remarks="Salary ₹25,000: Karnataka PT starts; basic ₹12,500 under any PF ceiling",
          look_for="PT ₹200. PF ₹1,500 in both months: the PF ceiling change does not touch him."),
        P("RB016", "Nisha Agarwal", "Female", BLR, date(2022, 10, 10), salary=[(APR1, 36000, REC)],
          remarks="Basic ₹18,000: between the old PF ceiling (₹15,000) and the new one (₹21,000)",
          look_for="PF ₹1,800 in November; ₹2,160 in December under the new ceiling (12% of ₹18,000)."),
        P("RB017", "Rahul Verma", "Male", BLR, date(2021, 1, 18), salary=[(APR1, 60000, REC)],
          remarks="Basic ₹30,000: above both PF ceilings",
          look_for="PF ₹1,800 in November; ₹2,520 in December (12% of the new ₹21,000 ceiling). Company PF the same."),
        P("RB018", "Aditi Joshi", "Female", BLR, date(2019, 7, 1), department="Leadership", designation="Design Director",
          salary=[(APR1, 200000, REC)],
          remarks="₹2,00,000 a month; opening balance given for April to October",
          look_for="Pay for the year {RB018_projected}, tax for the year {RB018_annual}, less {RB018_paid} already deducted "
                   "(opening balance), over 5 months = {RB018_nov_tds} TDS in November."),
        P("RB019", "Karthik Reddy", "Male", BLR, date(2018, 4, 2), department="Leadership", designation="Studio Head",
          salary=[(APR1, 205500, REC)], opening_given=False,
          remarks="₹2,05,500 a month; NO opening balance on purpose",
          look_for="The app assumes nothing was paid since April: pay for the year {RB019_projected}, TDS {RB019_nov_tds}. With his "
                   "real April to October pay entered it would be {RB019_real_tds} (see the 'TDS gap' sheet). Setup lists him as missing a balance."),
        P("RB020", "Amit Shah", "Male", BOM, date(2023, 3, 6), department="Sales", designation="Account Manager",
          salary=[(APR1, 45000, REC)],
          remarks="Works in Mumbai (Maharashtra PT)", look_for="Maharashtra PT for men above ₹10,000: ₹200 (₹300 in February)."),
        P("RB021", "Priyanka More", "Female", BOM, date(2024, 8, 19), department="Sales", designation="Sales Associate",
          salary=[(APR1, 24000, REC)],
          remarks="Works in Mumbai, woman, ₹24,000", look_for="Maharashtra PT for women starts above ₹25,000: ₹0."),
        P("RB022", "Naveen Kumar", "Male", BLR, date(2022, 2, 14), salary=[(APR1, 48000, REC)],
          missed_punch=[date(2026, 11, 18)],
          remarks="No check-in on Wed 18 Nov and no leave (a forgotten punch)",
          look_for="Suggested on the November page as a possible unpaid absence. Pay is not cut unless you add it "
                   "(then 1 day ÷ 30: gross {RB022_if_added} less)."),
        P("RB023", "Shruti Das", "Female", BLR, date(2023, 6, 26), salary=[(APR1, 52000, REC)],
          missed_punch=[date(2026, 12, 2), date(2026, 12, 3)],
          remarks="No check-in on Wed 2 and Thu 3 Dec and no leave",
          look_for="Suggested on the December page (2 days). If added: 2 ÷ 31 of ₹52,000, gross {RB023_if_added} less."),
        P("RB024", "Gaurav Malhotra", "Male", BLR, date(2026, 8, 3), salary=[(date(2026, 8, 3), 70000, date(2026, 7, 28))],
          remarks="Joined 3 Aug 2026; opening balance covers August to October only",
          look_for="Pay so far {RB024_opening} (part of August, then September and October). No TDS: under ₹12 lakh taxable."),
        P("RB025", "Tara Fernandes", "Female", BLR, date(2026, 11, 9), left=date(2026, 11, 27), salary=[(date(2026, 11, 9), 36000, date(2026, 11, 2))],
          remarks="Joins 9 Nov and leaves 27 Nov 2026 (both in the same month)",
          look_for="Paid 9 to 27 Nov: 19 of 30 days = {RB025_nov_gross}. Not in December."),
        P("RB026", "Imran Qureshi", "Male", BLR, date(2022, 5, 16), left=date(2026, 12, 15), salary=[(APR1, 55000, REC)],
          remarks="Last working day 15 Dec 2026",
          look_for="November: full ₹55,000. December: 15 of 31 days = {RB026_dec_gross}; December is his last month for TDS."),
    ]


FIRST = ["Aarav", "Ishaan", "Kabir", "Reyansh", "Vivaan", "Aditya", "Arjun", "Dhruv", "Harsh", "Kunal", "Nikhil", "Pranav",
         "Rajat", "Siddharth", "Tarun", "Varun", "Yash", "Zubin", "Abhay", "Chirag", "Deepak", "Eshan", "Gautam", "Hemant",
         "Aisha", "Bhavna", "Charu", "Diya", "Esha", "Gauri", "Ira", "Jaya", "Kavya", "Lavanya", "Mira", "Neha", "Ojasvi",
         "Pallavi", "Radhika", "Sakshi", "Tanvi", "Uma", "Vani", "Yamini", "Zoya", "Anjali", "Bina", "Chitra", "Deepa",
         "Gita", "Hema", "Indu", "Juhi", "Kirti", "Leela", "Madhu"]
LAST = ["Sharma", "Iyer", "Rao", "Nair", "Menon", "Reddy", "Kapoor", "Bhat", "Shetty", "Kamath", "Chopra", "Saxena",
        "Mishra", "Pandey", "Jain", "Kaur", "Gill", "Mehta", "Naidu", "Prasad"]
DEPTS = [("Design", "Designer"), ("Design", "Senior Designer"), ("Studio Ops", "Coordinator"), ("Sales", "Account Manager"),
         ("Finance", "Accountant"), ("Projects", "Project Manager")]
SALARIES = [22000, 26000, 28000, 30000, 32000, 34000, 38000, 40000, 44000, 46000, 50000, 54000, 58000, 64000, 70000,
            76000, 82000, 90000, 98000, 110000, 125000, 140000]


def ordinary_people() -> list[Person]:
    out = []
    for i in range(54):
        n = 27 + i
        gross = SALARIES[(i * 7) % len(SALARIES)]
        dept, title = DEPTS[i % len(DEPTS)]
        female = i % 2 == 1
        joined = date(2018 + i % 7, 1 + (i * 5) % 12, 1 + (i * 3) % 27)
        p = Person(f"RB{n:03d}", f"{FIRST[i]} {LAST[(i * 3) % len(LAST)]}", "Female" if female else "Male",
                   BOM if i % 9 == 4 else BLR, joined, department=dept, designation=title, salary=[(APR1, gross, REC)])
        if i % 6 == 2:
            p.leave.append(("Sick Leave", date(2026, 11, 2) + timedelta(days=i % 14), date(2026, 11, 2) + timedelta(days=i % 14), "Approved"))
        if i % 8 == 5:
            p.leave.append(("Earned Leave", date(2026, 12, 7), date(2026, 12, 9), "Approved"))
        out.append(p)
    return out


# --------------------------------------------------------------------------- the calculation (written from the rules)

def tax_for_year(taxable: Decimal) -> Decimal:
    rule = RULES[("income_tax", "IN")]
    taxable = max(D(0), taxable)
    tax, lower = D(0), D(0)
    for slab in rule["slabs"]:
        upper = D(slab["upto"]) if slab["upto"] is not None else None
        if taxable > lower:
            top = taxable if upper is None else min(taxable, upper)
            tax += (top - lower) * D(slab["rate"])
        if upper is None:
            break
        lower = upper
    limit = D(rule["rebate"]["income_upto"])
    if taxable <= limit:
        tax -= min(tax, D(rule["rebate"]["max_rebate"]))
    elif tax > taxable - limit:          # marginal relief just above 12 lakh
        tax = taxable - limit
    return rupee(tax * (1 + D(rule["cess_rate"])))   # no one here earns enough for a surcharge


def salary_rate(p: Person, period: str) -> dict[str, Decimal]:
    """Monthly rate by component for the month: each day at the salary in force that day."""
    start, end = month_dates(period)
    days = (end - start).days + 1
    revisions = sorted(p.salary)
    total = {c: D(0) for c in COMPONENTS}
    for day in days_between(start, end):
        current = [r for r in revisions if r[0] <= day]
        _, gross, _ = current[-1] if current else revisions[0]
        for c, v in split(gross).items():
            total[c] += D(v) / days
    return total


def unpaid_leave(p: Person, period: str) -> Decimal:
    start, end = month_dates(period)
    total = D(0)
    for kind, a, b, status in p.leave:
        if kind != "Leave Without Pay" or status != "Approved":
            continue
        inside = sum(1 for day in days_between(a, b) if start <= day <= end)
        total += D(inside)
    return total


def pt(p: Person, gross: Decimal, month: int) -> Decimal:
    if p.state == "KA":
        return D(200) if gross >= 25000 else D(0)
    if p.gender == "Male":       # Maharashtra
        return D(300 if month == 2 else 200) if gross > 10000 else D(175) if gross > 7500 else D(0)
    return D(300 if month == 2 else 200) if gross > 25000 else D(0)


def calculate(p: Person, period: str, history: dict[str, dict], pf_ceiling_from_rule: bool = True) -> dict | None:
    start, end = month_dates(period)
    if p.joined > end or (p.left and p.left < start):
        return None
    days = (end - start).days + 1
    rate = salary_rate(p, period)
    not_employed = sum(1 for day in days_between(start, end) if day < p.joined or (p.left and day > p.left))
    lop = unpaid_leave(p, period)
    unpaid = lop + not_employed
    earned = {}
    for c in COMPONENTS:
        cut = min(rupee(rate[c] * unpaid / days), rupee(rate[c]))
        earned[c] = rupee(rate[c]) - cut
    gross = sum(earned.values())
    contracted = sum(rupee(v) for v in rate.values())

    # PF: basic, plus what the Labour Codes 50% rule adds back, capped at the ceiling in force on the 1st of the month
    pf_rule = RULES[("pf", "IN")]
    y, m = label_month(period)
    ceiling = NEW_PF_CEILING if (pf_ceiling_from_rule and date(y, m, 1) >= NEW_PF_FROM) else D(pf_rule["wage_ceiling"])
    wage = earned["basic"]
    exclusions = gross - earned["basic"]
    if exclusions > gross * D("0.5"):
        wage += exclusions - gross * D("0.5")
    pf_emp = rupee(min(wage, ceiling) * D(pf_rule["employee_rate"]))
    pf_er = rupee(min(wage, ceiling) * D(pf_rule["employer_rate"]))
    eps = min(rupee(min(wage, D(pf_rule["eps_wage_ceiling"])) * D(pf_rule["eps_rate"])), pf_er)

    esi_rule = RULES[("esi", "IN")]
    # ESI: covered when this month's pay is within the ceiling, and coverage then continues to the end of the
    # contribution period (April-September, October-March) even if pay goes over it.
    def contribution_period(q: str) -> tuple[int, int]:  # (year it starts, 4 or 10)
        qy, qm = label_month(q)
        return (qy, 4) if 4 <= qm <= 9 else (qy if qm >= 10 else qy - 1, 10)
    continuing = any(h.get("esi", 0) > 0 and q < period and contribution_period(q) == contribution_period(period)
                     for q, h in history.items())
    esi = gross > 0 and (gross <= D(esi_rule["threshold"]) or continuing)
    esi_emp = rupee_up(gross * D(esi_rule["employee_rate"])) if esi else D(0)
    esi_er = rupee_up(gross * D(esi_rule["employer_rate"])) if esi else D(0)
    pt_amount = pt(p, gross, m)

    # TDS: pay so far this year + this month + the pay expected in each month left (a leaving month pays only the days
    # worked), less the standard deduction
    opening_gross, opening_tds = (D(p.opening[0]), D(p.opening[1])) if p.opening else (D(0), D(0))
    paid_gross = opening_gross + sum(h["gross"] for h in history.values())
    tds_paid = opening_tds + sum(h["tds"] for h in history.values())
    last = "2027-03"
    if p.left and period_of(p.left) < last:
        last = max(period_of(p.left), period)
    future, q = D(0), next_period(period)
    while q <= last:
        q_start, q_end = month_dates(q)
        q_days = (q_end - q_start).days + 1
        q_rate = salary_rate(p, q)
        q_away = sum(1 for day in days_between(q_start, q_end) if day < p.joined or (p.left and day > p.left))
        future += sum(rupee(q_rate[c]) - min(rupee(q_rate[c] * q_away / q_days), rupee(q_rate[c])) for c in COMPONENTS)
        q = next_period(q)
    future = rupee(future)
    months_left = (int(last[:4]) * 12 + int(last[5:])) - (y * 12 + m) + 1
    projected = paid_gross + gross + future
    taxable = max(D(0), projected - D(RULES[("income_tax", "IN")]["standard_deduction"]))
    annual = tax_for_year(taxable)
    tds = max(D(0), rupee((annual - tds_paid) / months_left))

    deductions = pf_emp + esi_emp + pt_amount + tds
    return {
        "days_in_month": days, "not_employed_days": not_employed, "unpaid_leave_days": lop,
        "monthly_salary": contracted, "unpaid_cut": contracted - gross, "gross": gross,
        "basic": earned["basic"], "hra": earned["hra"], "special_allowance": earned["special_allowance"],
        "pf_wage": rupee(wage), "pf": pf_emp, "esi": esi_emp, "pt": pt_amount, "tds": tds, "net_pay": gross - deductions,
        "pf_company": pf_er, "eps": eps, "esi_company": esi_er, "cost_to_company": gross + pf_er + esi_er,
        "tds_projected_income": projected, "tds_taxable_income": taxable, "tds_annual_tax": annual, "tds_already_deducted": tds_paid,
        "tds_months_left": months_left,
    }


def april_to_october(p: Person) -> tuple[Decimal, Decimal]:
    """Pay and TDS of the months before November, worked out the same way: what an earlier payroll paid and deducted.
    A leaving date in November was not known then, so those months projected the whole year."""
    saved = p.opening, p.left
    p.opening, p.left = None, None
    history, q = {}, "2026-04"
    while q < MONTHS[0]:
        r = calculate(p, q, history)
        if r:
            history[q] = r
        q = next_period(q)
    p.opening, p.left = saved
    return sum((h["gross"] for h in history.values()), D(0)), sum((h["tds"] for h in history.values()), D(0))


def run_months(people: list[Person]):
    results = {period: {} for period in MONTHS}
    without_rule = {}
    for p in people:
        gross, tds = april_to_october(p)
        if p.opening_given and gross:
            p.opening = (gross, tds)
        history = {}
        for period in MONTHS:
            r = calculate(p, period, history)
            if r is None:
                continue
            results[period][p.code] = r
            if period == "2026-12":
                without_rule[p.code] = calculate(p, period, history, pf_ceiling_from_rule=False)
            history[period] = r
    return results, without_rule


# --------------------------------------------------------------------------- files

HEAD = Font(bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor="2F4F4F")
SPECIAL_FILL = PatternFill("solid", fgColor="FFF4CC")


def sheet(wb, title, headers, rows, special=0, widths=None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.font, cell.fill = HEAD, HEAD_FILL
    for i, row in enumerate(rows):
        ws.append(row)
        if i < special:
            for cell in ws[ws.max_row]:
                cell.fill = SPECIAL_FILL
    for col, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(col)].width = (widths or {}).get(h, max(12, min(60, len(h) + 4)))
        for cell in ws[get_column_letter(col)][1:]:
            if isinstance(cell.value, (date, datetime)):
                cell.number_format = "dd-mm-yyyy"
    ws.freeze_panes = "A2"
    return ws


def workbook(title, headers, rows, special, widths=None) -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    sheet(wb, title, headers, rows, special, widths)
    return wb


def working_days(a: date, b: date):
    return [d for d in days_between(a, b) if d.weekday() < 5]


def write_inputs(people: list[Person], special: int):
    remarks = {"Remarks": 70}
    workbook("Employees", ["Employee ID", "Employee Name", "Gender", "Work Location", "Date of Joining", "Date of Leaving",
                           "Department", "Designation", "Remarks"],
             [[p.code, p.name, p.gender, p.city, p.joined, p.left, p.department, p.designation, p.remarks] for p in people],
             special, {**remarks, "Employee Name": 22, "Work Location": 24}).save(OUT / "1_Employees.xlsx")

    rows, n_special = [], 0
    for i, p in enumerate(people):
        for j, (start, gross, recorded) in enumerate(sorted(p.salary)):
            parts = split(gross)
            note = p.remarks if (i < special and (len(p.salary) == 1 or j == len(p.salary) - 1)) else ""
            rows.append([p.code, start, parts["basic"], parts["hra"], parts["special_allowance"], gross, recorded, note])
            n_special += i < special
    workbook("Salary", ["Employee ID", "Effective From", "Basic", "HRA", "Special Allowance", "Monthly Gross", "Recorded On", "Remarks"],
             rows, n_special, remarks).save(OUT / "2_Salary.xlsx")

    leave_people = [p for p in people if p.leave]
    rows = [[p.code, kind, a, b, (b - a).days + 1, status, p.remarks if i < special else ""]
            for i, p in enumerate(people) for kind, a, b, status in p.leave]
    special_rows = sum(len(p.leave) for p in people[:special])
    workbook("Leave", ["Employee ID", "Leave Type", "From", "To", "Days", "Status", "Remarks"], rows, special_rows,
             {**remarks, "Leave Type": 20}).save(OUT / "3_Leave.xlsx")
    assert leave_people

    # Daily check-ins for both months: working days employed, not on leave (any type), not a missed punch.
    first, last = month_dates(MONTHS[0])[0], month_dates(MONTHS[-1])[1]
    rows = []
    for day in working_days(first, last):
        for k, p in enumerate(people):
            if day < p.joined or (p.left and day > p.left) or day in p.missed_punch:
                continue
            if any(a <= day <= b and status == "Approved" for _, a, b, status in p.leave):
                continue
            minute = (k * 7 + day.day * 3) % 50
            rows.append([p.code, day, f"09:{minute:02d}", f"18:{(minute + 13) % 60:02d}"])
    workbook("Attendance", ["Employee ID", "Date", "Check-in", "Check-out"], rows, 0).save(OUT / "4_Attendance.xlsx")

    with open(OUT / "opening_balances.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["Employee ID", "Gross paid this year", "TDS this year"])
        for p in people:
            if p.opening:
                w.writerow([p.code, int(p.opening[0]), int(p.opening[1])])


COLUMNS = [("days_in_month", "Days in month"), ("not_employed_days", "Days not employed"), ("unpaid_leave_days", "Unpaid leave days"),
           ("monthly_salary", "Monthly salary"), ("unpaid_cut", "Unpaid cut"), ("gross", "Gross pay"), ("pf", "PF"), ("esi", "ESI"),
           ("pt", "Prof. tax"), ("tds", "TDS"), ("net_pay", "Net pay"), ("pf_company", "PF (company)"), ("esi_company", "ESI (company)"),
           ("cost_to_company", "Cost to company"), ("pf_wage", "PF wage"), ("tds_projected_income", "TDS: pay for the year"),
           ("tds_annual_tax", "TDS: tax for the year"), ("tds_already_deducted", "TDS: already deducted"), ("tds_months_left", "TDS: months left")]


def as_number(v):
    return int(v) if isinstance(v, Decimal) and v == v.to_integral_value() else float(v) if isinstance(v, Decimal) else v


def write_expected(people: list[Person], special: int, results, without_rule):
    by_code = {p.code: p for p in people}
    wb = Workbook()
    ws = wb.active
    ws.title = "How to use"
    steps = [
        "Riverbend Interiors Pvt Ltd (made-up): people joining and leaving mid-month, a government rule change, and the numbers to expect.",
        "",
        "1. Payroll app (http://localhost:8080) as maker / maker123 -> company picker -> Add a company: Riverbend Interiors Pvt Ltd,",
        "   Karnataka, Bengaluru, first payroll month November 2026. Registration numbers can stay empty.",
        "2. Pay settings: keep the defaults (one day's pay = monthly pay ÷ days in the month, PF on the first ₹15,000 of basic,",
        "   ESI on gross this month). When opening a month, keep its day count at 'days in the month' (do not type 22 or 26).",
        "3. HR data -> upload 1_Employees.xlsx, 2_Salary.xlsx, 3_Leave.xlsx, 4_Attendance.xlsx in their four boxes.",
        "   Confirm each field mapping: the Remarks column is ignored. Accept the suggested unpaid leave type (Leave Without Pay).",
        "4. Check the data and mark it as right.",
        "5. Opening balances: upload opening_balances.csv. RB019 is left out on purpose and is listed as missing.",
        "6. Open November 2026: review the data, Calculate. Compare with the 'November 2026' sheet.",
        "   The highlighted rows are the special cases; the 'Special cases' sheet says what to look for in each.",
        "   Possible absences (attendance): one suggestion, RB022 on 18 Nov. Leave it as it is: the expected numbers do not add it.",
        "7. Submit; sign in as checker / checker123 and approve: November is final.",
        "8. Government change (made up for the demo): Rules & settings -> PF -> + New version. Starts on 01-12-2026,",
        "   title 'PF wage ceiling ₹21,000 (demo)', change the wage ceiling from 15000 to 21000 (leave the pension ceiling at 15000). Save.",
        "   Government rules are shared by every company: remove this version afterwards (Remove on its card) or the other",
        "   companies' months from December will use it too.",
        "9. Check it does not touch the past: open November -> What if? -> Run a draft and compare: no changes (the rule starts 1 Dec).",
        "10. Upload the same four files again and mark the data as right: the app pays a month only on HR data read after the",
        "    previous month was approved, so it refuses December on the old read.",
        "11. Open December 2026 as maker, review, Calculate. Compare with the 'December 2026' sheet.",
        "    'PF rule change' lists everyone whose PF changes, before and after. Attendance suggests 2 Dec and 3 Dec for RB023.",
        "",
        "Amounts are whole rupees. Expected numbers assume the attendance suggestions are NOT added (adding one changes pay).",
        "Worked out by scripts/make_riverbend_sample.py from the rules in data/rules/statutory_in.json; it does not use the app's engine.",
        "backend/tests/test_riverbend_sample.py runs these files through the app (upload to December) and checks every number.",
    ]
    for line in steps:
        ws.append([line])
    ws.column_dimensions["A"].width = 140
    ws["A1"].font = Font(bold=True, size=13)

    sheet(wb, "Special cases", ["Employee ID", "Name", "What is special", "What to look for",
                                "Nov gross", "Nov net pay", "Dec gross", "Dec net pay"],
          [[p.code, p.name, p.remarks, p.look_for,
            *(as_number(results[m][p.code][k]) if p.code in results[m] else "not in this month" for m in MONTHS for k in ("gross", "net_pay"))]
           for p in people[:special]], special, {"What is special": 50, "What to look for": 90, "Name": 18})
    wsx = wb["Special cases"]
    for row in wsx.iter_rows(min_row=2):
        for cell in row[2:4]:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    for period, title in zip(MONTHS, ("November 2026", "December 2026")):
        start, end = month_dates(period)
        rows = []
        for p in people:
            r = results[period].get(p.code)
            if r:
                rows.append([p.code, p.name, p.state] + [as_number(r[k]) for k, _ in COLUMNS])
        total = ["Total", f"{len(rows)} people", ""] + [
            sum(D(str(row[3 + i])) for row in rows) if k in ("gross", "pf", "esi", "pt", "tds", "net_pay", "pf_company",
                                                              "esi_company", "cost_to_company", "unpaid_cut") else "" for i, (k, _) in enumerate(COLUMNS)]
        total = [as_number(v) for v in total]
        n_special = sum(1 for p in people[:special] if p.code in results[period])
        s = sheet(wb, title, ["Employee ID", "Name", "State"] + [h for _, h in COLUMNS], rows + [total], n_special, {"Name": 18})
        s.append([])
        s.append([f"Pay period {start.day} {calendar.month_name[start.month]} to {end.day} {calendar.month_name[end.month]} {end.year}"
                  f" ({(end - start).days + 1} days)"])
        for cell in s[len(rows) + 2]:
            cell.font = Font(bold=True)

    rows = []
    for code, after in results["2026-12"].items():
        before = without_rule[code]
        if before["pf"] != after["pf"]:
            rows.append([code, by_code[code].name, as_number(after["pf_wage"]), as_number(before["pf"]), as_number(after["pf"]),
                         as_number(after["pf"] - before["pf"]), as_number(before["net_pay"]), as_number(after["net_pay"]),
                         as_number(before["cost_to_company"]), as_number(after["cost_to_company"])])
    unchanged = len(results["2026-12"]) - len(rows)
    s = sheet(wb, "PF rule change", ["Employee ID", "Name", "PF wage (basic)", "PF before (₹15,000 ceiling)", "PF after (₹21,000 ceiling)",
                                     "Change", "Net pay before", "Net pay after", "Cost to company before", "Cost to company after"],
              rows, 0, {"Name": 18})
    s.append([])
    s.append([f"{len(rows)} people's PF changes in December 2026; {unchanged} are unchanged (basic of ₹15,000 or less)."])
    s.append([f"Employee PF for the month: {inr(sum(r[3] for r in rows))} before -> {inr(sum(r[4] for r in rows))} after, for these people. "
              "TDS does not change (the new regime gives no deduction for PF). November is not affected: the rule starts on 1 December."])

    # The TDS gap: what RB019's TDS would be with his real pay from April
    p = by_code["RB019"]
    real_gross, real_tds = april_to_october(p)
    with_balance = calculate(Person(**{**p.__dict__, "opening": (real_gross, real_tds)}), "2026-11", {})
    s = sheet(wb, "TDS gap (RB019)", ["", "No opening balance (as uploaded)", "With April to October entered"], [
        ["Pay counted for the year", as_number(results["2026-11"]["RB019"]["tds_projected_income"]), as_number(with_balance["tds_projected_income"])],
        ["Tax for the year", as_number(results["2026-11"]["RB019"]["tds_annual_tax"]), as_number(with_balance["tds_annual_tax"])],
        ["TDS already deducted", 0, as_number(real_tds)],
        ["November TDS", as_number(results["2026-11"]["RB019"]["tds"]), as_number(with_balance["tds"])],
    ], 0, {"": 28})
    s.append([])
    s.append(["Without the balance the app treats him as earning nothing from April to October, so it takes no TDS. "
              f"To see the right number, add 'RB019,{real_gross},{real_tds}' to opening_balances.csv before November is submitted."])
    wb.save(OUT / "expected_results.xlsx")

    # The same numbers as data, for the test that runs the files through the app
    (OUT / "expected.json").write_text(json.dumps(
        {period: {code: {k: str(v) for k, v in r.items()} for code, r in results[period].items()} for period in MONTHS},
        indent=1, sort_keys=True) + "\n")


def inr(x) -> str:
    """₹1,23,456: Indian digit grouping."""
    n = int(rupee(x))
    text = str(abs(n))
    if len(text) > 3:
        head, tail = text[:-3], text[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        text = ",".join(([head] if head else []) + groups) + "," + tail
    return ("-" if n < 0 else "") + "₹" + text


def explain(people: list[Person], results) -> None:
    """Fill the numbers into the "what to look for" notes, from the calculation, so the notes cannot drift."""
    by_code = {p.code: p for p in people}
    nov, dec = results["2026-11"], results["2026-12"]

    def gross_if_unpaid(code, period, day, history):
        p = by_code[code]
        extra = Person(**{**p.__dict__, "leave": p.leave + [("Leave Without Pay", day, day, "Approved")]})
        return results[period][code]["gross"] - calculate(extra, period, history)["gross"]

    rb019 = by_code["RB019"]
    real = april_to_october(rb019)
    values = {
        "RB004_projected": inr(nov["RB004"]["tds_projected_income"]), "RB004_paid": inr(nov["RB004"]["tds_already_deducted"]),
        "RB001_nov_gross": inr(nov["RB001"]["gross"]), "RB002_nov_gross": inr(nov["RB002"]["gross"]),
        "RB004_nov_gross": inr(nov["RB004"]["gross"]), "RB004_annual": inr(nov["RB004"]["tds_annual_tax"]),
        "RB004_nov_tds": inr(nov["RB004"]["tds"]), "RB010_nov_gross": inr(nov["RB010"]["gross"]),
        "RB025_nov_gross": inr(nov["RB025"]["gross"]), "RB026_dec_gross": inr(dec["RB026"]["gross"]),
        "RB018_projected": inr(nov["RB018"]["tds_projected_income"]), "RB018_annual": inr(nov["RB018"]["tds_annual_tax"]),
        "RB018_paid": inr(nov["RB018"]["tds_already_deducted"]), "RB018_nov_tds": inr(nov["RB018"]["tds"]),
        "RB019_projected": inr(nov["RB019"]["tds_projected_income"]), "RB019_nov_tds": inr(nov["RB019"]["tds"]),
        "RB019_real_tds": inr(calculate(Person(**{**rb019.__dict__, "opening": real}), "2026-11", {})["tds"]),
        "RB022_if_added": inr(gross_if_unpaid("RB022", "2026-11", date(2026, 11, 18), {})),
        "RB023_if_added": inr(results["2026-12"]["RB023"]["gross"] - calculate(
            Person(**{**by_code["RB023"].__dict__, "leave": [("Leave Without Pay", date(2026, 12, 2), date(2026, 12, 3), "Approved")]}),
            "2026-12", {"2026-11": nov["RB023"]})["gross"]),
        "RB024_opening": inr(by_code["RB024"].opening[0]),
    }
    for p in people:
        p.look_for = p.look_for.format(**values)
    assert dec["RB003"] and "RB003" not in nov and "RB004" not in dec and "RB025" not in dec and "RB005" not in dec


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    special = special_cases()
    people = special + ordinary_people()
    assert len({p.code for p in people}) == len(people) == 80
    results, without_rule = run_months(people)
    explain(people, results)
    write_inputs(people, len(special))
    write_expected(people, len(special), results, without_rule)
    for period in MONTHS:
        r = results[period]
        print(f"{period}: {len(r)} people, gross {sum(x['gross'] for x in r.values()):,}, net {sum(x['net_pay'] for x in r.values()):,}, "
              f"TDS {sum(x['tds'] for x in r.values()):,}")
    print(f"written to {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
