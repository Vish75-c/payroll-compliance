"""Sample company for trying pay cycles and pay groups: Kestrel Analytics Pvt Ltd, Bengaluru (made up).

    .venv/bin/python scripts/make_kestrel_data.py      ->  data/kestrel/

Kestrel moves to this system in November 2026. Its analysts and engineers are paid on a 26th-to-25th cycle (the pay
group "Shift staff", paid on the 28th), so their November is 26 Oct to 25 Nov; the director is on the calendar month,
paid on the 1st. The old payroll already ran those cycles, which is what the opening balances assume. For November:

* KS003 has two unpaid leaves: 21-22 Oct (before November's cycle: the old payroll cut it in October) and
  27-28 Oct (inside November's cycle).
* KS004 joined on 28 Oct (inside November's cycle, before the calendar month) and KS005 on 17 Nov. KS004 also took
  half a day unpaid on 10 Nov, written the way Horilla exports it (Start/End Date Breakdown: First Half).
* KS001 earns ₹2,50,000 a month and has had TDS every month of the year.
* KS002 earned ₹90,000 (no tax: under ₹12 lakh) and gets ₹1,40,000 from 1 Nov 2026, which takes the year over
  ₹12 lakh: TDS starts this month.

Writes seed.json (the facts), employees.xlsx, salaries.xlsx, leave.csv, opening_balances.csv, and expected.json with
README.md: November worked out by the calculation (payroll_engine) with the two pay groups, and with one cycle for
everyone, to compare with the app. Deterministic: running it again gives the same files.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/kestrel"
sys.path.insert(0, str(ROOT / "backend"))

from payroll_engine.engine import calculate_employee  # noqa: E402
from payroll_engine.periods import Period, cycle_window, overlap_days, pay_date  # noqa: E402

FY_START = date(2026, 4, 1)
FIRST_MONTH = "2026-11"
START_DAY = 26

COMPANY = {"code": "kestrel", "name": "Kestrel Analytics Pvt Ltd", "city": "Bengaluru", "state": "KA",
           "pan": "AABCK5678L", "tan": "BLRK05678C", "pf_establishment_code": "KNBNG0056789000",
           "esi_employer_code": "53000567890001001", "pay_day": 1, "first_month": FIRST_MONTH,
           "settings": {"lop": {"divisor": "days_in_month", "day_rate_rounding": None}},
           # Everyone else (the default group) is on the calendar month, paid on the company's day (the 1st).
           "pay_groups": [{"name": "Management", "mode": "calendar", "start_day": 1, "pay_day": 1,
                           "covers": {"departments": ["Leadership"]}},
                          {"name": "Shift staff", "mode": "shifted", "start_day": START_DAY, "pay_day": 28,
                           "covers": {"departments": ["Analytics", "Engineering"]}}]}

# The "Pay group" column of the HR file: who the HR tool itself puts in a group (the rest go by the groups' rules).
PAY_GROUP_COLUMN = {"KS005": "Shift staff"}

# code, name, gender, joined, department, designation, monthly gross, what it tests
PEOPLE = [
    ("KS001", "Arvind Rao", "M", "2019-04-15", "Leadership", "Director", 250000, "TDS every month of the year"),
    ("KS002", "Meera Joshi", "F", "2021-07-05", "Analytics", "Senior Analyst", 90000, "raise from 1 Nov: TDS starts this month"),
    ("KS003", "Sameer Kulkarni", "M", "2022-02-14", "Analytics", "Analyst", 45000, "unpaid leave before and inside the cycle"),
    ("KS004", "Nikhil Verma", "M", "2026-10-28", "Engineering", "Data Engineer", 60000, "new joiner, inside November's cycle but before 1 Nov"),
    ("KS005", "Anjali Desai", "F", "2026-11-17", "Analytics", "Analyst", 42000, "new joiner this month"),
]
# code, effective from, new monthly gross, approved on
RAISES = [("KS002", "2026-11-01", 140000, "2026-10-28")]
# code, type, from, first day (Horilla's Start Date Breakdown), to, last day, days, status
LEAVE = [
    ("KS003", "Loss of Pay", "2026-10-21", "Full Day", "2026-10-22", "Full Day", 2, "Approved"),  # before 26 Oct: October's cycle
    ("KS003", "Loss of Pay", "2026-10-27", "Full Day", "2026-10-28", "Full Day", 2, "Approved"),  # inside November's cycle
    ("KS004", "Loss of Pay", "2026-11-10", "First Half", "2026-11-10", "First Half", 0.5, "Approved"),  # half a day
]

# The setups the expected numbers are worked out for: the two pay groups (as the README sets it up), and one cycle
# for everyone (the default group alone).
VARIANTS = {
    "groups": {"default": {"mode": "calendar", "start_day": 1}, "groups": COMPANY["pay_groups"]},
    "shifted": {"default": {"mode": "shifted", "start_day": START_DAY}, "groups": []},
    "attendance_cutoff": {"default": {"mode": "attendance_cutoff", "start_day": START_DAY}, "groups": []},
    "calendar": {"default": {"mode": "calendar", "start_day": 1}, "groups": []},
}


def split(gross: int) -> dict:
    basic, hra = gross // 2, gross // 5
    return {"basic": basic, "hra": hra, "special_allowance": gross - basic - hra}


def new_regime_tax(taxable: Decimal) -> Decimal:
    """Tax for the year under the new regime, as the old payroll estimated it (same slabs as the stored rule)."""
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


def rupee(x) -> Decimal:
    return Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def opening_balances() -> list[tuple[str, Decimal, Decimal]]:
    """What the old payroll paid and deducted for its April to October cycles (26 Mar to 25 Oct 2026)."""
    out = []
    for code, _, _, joined, _, _, gross, _ in PEOPLE:
        if date.fromisoformat(joined) > date(2026, 10, 25):
            continue  # joined after the old payroll's last cycle
        paid = Decimal(gross) * 7
        october = cycle_window(Period(2026, 10), START_DAY)
        paid -= rupee(Decimal(gross) * unpaid_days(code, october) / october.days)
        tds = rupee(new_regime_tax(max(Decimal(0), Decimal(gross) * 12 - 75000)) / 12 * 7)
        out.append((code, paid, tds))
    return out


def unpaid_days(code: str, window) -> Decimal:
    """Unpaid days inside ``window``: every day 1, a half first or last day 0.5 (as Horilla counts a leave)."""
    total = Decimal(0)
    for lv_code, kind, start, start_part, end, end_part, days, status in LEAVE:
        if lv_code != code or kind != "Loss of Pay" or status != "Approved":
            continue
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first == last:
            value = Decimal("0.5") if "Half" in start_part or "Half" in end_part else Decimal(1)
            total += value if window.start <= first <= window.end else 0
            continue
        total += overlap_days(first, last, window)
        total -= Decimal("0.5") * sum(1 for day, part in ((first, start_part), (last, end_part))
                                      if "Half" in part and window.start <= day <= window.end)
    return total


def windows(cycle: dict, period: Period):
    """(salary, attendance) dates of a month under a cycle."""
    if cycle["mode"] == "shifted":
        w = cycle_window(period, cycle["start_day"])
        return w, w
    if cycle["mode"] == "attendance_cutoff":
        return cycle_window(period, 1), cycle_window(period, cycle["start_day"])
    return cycle_window(period, 1), cycle_window(period, 1)


def group_of(code: str, department: str, variant: dict) -> dict:
    named = PAY_GROUP_COLUMN.get(code)
    for g in variant["groups"]:
        if g["name"] == named or (not named and department in g["covers"].get("departments", [])):
            return {"code": g["name"].lower().replace(" ", "-"), "default": False, **g}
    for g in variant["groups"]:
        if department in g["covers"].get("departments", []):
            return {"code": g["name"].lower().replace(" ", "-"), "default": False, **g}
    return {"code": "default", "name": "Everyone else", "default": True, **variant["default"], "pay_day": COMPANY["pay_day"]}


def tds_due(deducted: Period) -> str:
    return (date(deducted.year, 4, 30) if deducted.month == 3 else date(deducted.next().year, deducted.next().month, 7)).isoformat()


def expected(versions: list[dict], openings: dict) -> dict:
    """November 2026 for each setup, worked out by the calculation (each person on their pay group's dates)."""
    period = Period.parse(FIRST_MONTH)
    out = {}
    for name, variant in VARIANTS.items():
        rows, tds_months = [], {}
        for code, pname, _, joined, department, _, gross, case in PEOPLE:
            group = group_of(code, department, variant)
            salary_window, attendance_window = windows(group, period)
            if date.fromisoformat(joined) > salary_window.end:
                continue
            pay_window = {"group": group["code"], "group_name": group["name"], "default": group["default"],
                          "salary_from": salary_window.start.isoformat(), "salary_to": salary_window.end.isoformat(),
                          "basis_days": salary_window.days, "attendance_from": attendance_window.start.isoformat(),
                          "attendance_to": attendance_window.end.isoformat(), "pay_day": group["pay_day"], "transition": False}
            comp = [{"effective_from": max(joined, FY_START.isoformat()), "components": {k: str(v) for k, v in split(gross).items()}}]
            comp += [{"effective_from": eff, "components": {k: str(v) for k, v in split(new).items()}} for c, eff, new, _ in RAISES if c == code]
            opening = openings.get(code)
            emp = {"code": code, "name": pname, "work_state": "KA", "date_of_joining": joined, "compensation": comp,
                   "unpaid_days": str(unpaid_days(code, attendance_window)), "one_time": [], "arrears_basis": [], "pay_window": pay_window,
                   "ytd": {"opening_gross": str(opening[0]) if opening else "0", "opening_tds": str(opening[1]) if opening else "0", "months": []}}
            a = calculate_employee(emp, period, versions, {"state": "KA"})["amounts"]
            paid_on = pay_date(salary_window.end, group["pay_day"])
            if Decimal(a.get("tds", "0")):
                tds_months.setdefault(str(Period.of(paid_on)), Decimal(0))
                tds_months[str(Period.of(paid_on))] += Decimal(a["tds"])
            rows.append({"code": code, "name": pname, "case": case, "pay_group": group["name"], "paid_for": salary_window.label,
                         "unpaid_counted": attendance_window.label, "pay_date": paid_on.isoformat(), **{k: a.get(k, "0") for k in (
                             "days_in_month", "not_employed_days", "unpaid_days", "fixed_gross", "lop_deduction", "gross",
                             "pf_employee", "esi_employee", "pt", "tds", "net_pay")}})
        tds = {(m if len(tds_months) > 1 else ""): {"total": str(v), "due": tds_due(Period.parse(m))} for m, v in sorted(tds_months.items())}
        out[name] = {"employees": rows, "dues": {"tds": tds, "pf": date(2026, 12, 15).isoformat()}}
    return out


def styled(wb):
    for cell in wb.active[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E7F0E9")
    for column in wb.active.columns:
        wb.active.column_dimensions[column[0].column_letter].width = max(12, max(len(str(c.value or "")) for c in column) + 2)
        for c in column[1:]:
            if isinstance(c.value, date):
                c.number_format = "DD-MM-YYYY"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    employees = [{"code": c, "name": n, "gender": g, "location": "Bengaluru, Karnataka", "doj": j, "department": dept,
                  "designation": t, **({"pay_group": PAY_GROUP_COLUMN[c]} if c in PAY_GROUP_COLUMN else {})}
                 for c, n, g, j, dept, t, _, _ in PEOPLE]
    salaries = []
    for code, _, _, joined, _, _, gross, _ in PEOPLE:
        start = max(joined, FY_START.isoformat())
        salaries.append({"code": code, "effective_from": start, **split(gross),
                         "recorded_on": "2026-03-27" if start == FY_START.isoformat() else joined})
    for code, start, gross, recorded in RAISES:
        salaries.append({"code": code, "effective_from": start, **split(gross), "recorded_on": recorded})
    leave = [{"code": c, "type": k, "from": f, "start_breakdown": fb, "to": t, "end_breakdown": tb, "days": d, "status": s}
             for c, k, f, fb, t, tb, d, s in LEAVE]
    openings = opening_balances()
    seed = {"_comment": "Facts of the pay-cycle sample company; files in this folder are made from it by scripts/make_kestrel_data.py.",
            "company": COMPANY, "employees": employees, "salaries": salaries, "leave": leave}
    (OUT / "seed.json").write_text(json.dumps(seed, indent=1, ensure_ascii=False) + "\n")

    wb = Workbook()
    wb.active.title = "Staff"
    wb.active.append(["Emp Code", "Full Name", "Sex", "Location", "DOJ", "Dept", "Job Title", "Pay Group"])
    for e in employees:
        wb.active.append([e["code"], e["name"], {"F": "Female", "M": "Male"}[e["gender"]], "Bengaluru",
                          date.fromisoformat(e["doj"]), e["department"], e["designation"], PAY_GROUP_COLUMN.get(e["code"], "")])
    styled(wb)
    wb.save(OUT / "employees.xlsx")

    wb = Workbook()
    wb.active.title = "Pay"
    wb.active.append(["Emp Code", "With Effect From", "Basic Pay", "House Rent Allowance", "Special Allowance", "Approved On"])
    for s in salaries:
        wb.active.append([s["code"], date.fromisoformat(s["effective_from"]), s["basic"], s["hra"], s["special_allowance"],
                          date.fromisoformat(s["recorded_on"])])
    styled(wb)
    wb.save(OUT / "salaries.xlsx")

    with open(OUT / "leave.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Emp Code", "Leave Category", "Leave From", "Start Date Breakdown", "Leave To", "End Date Breakdown",
                    "No of Days", "Approval Status"])
        for lv in leave:
            w.writerow([lv["code"], lv["type"], lv["from"], lv["start_breakdown"], lv["to"], lv["end_breakdown"], lv["days"], lv["status"]])

    with open(OUT / "opening_balances.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Employee ID", "Gross paid this year", "TDS this year"])
        for row in openings:
            w.writerow(row)

    statutory = [dict(v, id=str(i + 1), scope="statutory")
                 for i, v in enumerate(json.loads((ROOT / "data/rules/statutory_in.json").read_text())["versions"])]
    results = expected(statutory, {c: (g, t) for c, g, t in openings})
    (OUT / "expected.json").write_text(json.dumps({"period": FIRST_MONTH, "pay_day": COMPANY["pay_day"], "variants": results},
                                                  indent=1, ensure_ascii=False) + "\n")
    (OUT / "README.md").write_text(readme(results, openings))
    print(f"wrote {OUT}/")


def inr(x) -> str:
    """₹2,50,000: Indian grouping; a dash for nothing."""
    n = int(Decimal(x))
    if not n:
        return "–"
    digits = str(n)
    head, tail = digits[:-3], digits[-3:]
    while len(head) > 2:
        tail = f"{head[-2:]},{tail}"
        head = head[:-2]
    return f"₹{head},{tail}" if head else f"₹{tail}"


def readme(results: dict, openings) -> str:
    lines = [
        "# Kestrel Analytics: sample data for pay cycles and pay groups",
        "",
        "Made-up company. Made by `scripts/make_kestrel_data.py`, which also works out the expected November 2026 below",
        "with the calculation itself.",
        "",
        "## Set it up in the app",
        "",
        "1. **Add a company** with these details: Kestrel Analytics Pvt Ltd · Karnataka · Bengaluru · PAN AABCK5678L ·",
        "   TAN BLRK05678C · PF office KNBNG, number 0056789, extension 000 · ESI 53000567890001001 · salary paid on day **1** ·",
        "   first payroll month **November 2026**.",
        "2. **Pay settings:** leave the pay cycle as **Calendar month** (that is the default group, \"Everyone else\").",
        "3. **Rules & settings → Pay groups → + New pay group**, twice:",
        "   * **Management**: cycle **Calendar month**, paid on the **1st**, covers department **Leadership**.",
        f"   * **Shift staff**: cycle **Shifted**, starts on day **{START_DAY}**, paid on the **28th**, covers departments",
        "     **Analytics** and **Engineering**.",
        "4. **HR data:** upload `employees.xlsx`, `salaries.xlsx` and `leave.csv` together, confirm the columns (the leave file",
        "   has Horilla's *Start Date Breakdown* / *End Date Breakdown*: First Half is a half day), mark the data as right.",
        "5. **Employees:** KS001 is in *Management* (rule: Department Leadership), KS002–KS004 in *Shift staff* (rule:",
        "   Department), and KS005 in *Shift staff* from the HR file's own **Pay Group** column (\"HR data\"). To show a hand",
        "   setting, tick someone and use *Move to pay group* from a month that is not final.",
        "6. **Opening balances:** upload `opening_balances.csv` (April to October from the old payroll).",
        "7. **First month:** open November 2026 and calculate.",
        "",
        "To see one cycle for everyone instead, skip step 3 and pick that cycle in step 2 (a second company: a cycle change",
        "can only start from a month that is not final yet).",
        "",
        "## What each person tests",
        "",
        "| Code | Name | Department | Joined | Monthly pay | Case |",
        "|---|---|---|---|---|---|",
    ]
    for code, name, _, joined, dept, _, gross, case in PEOPLE:
        raise_text = "".join(f" → {inr(new)} from {date.fromisoformat(eff):%-d %b}" for c, eff, new, _ in RAISES if c == code)
        lines.append(f"| {code} | {name} | {dept} | {date.fromisoformat(joined):%-d %b %Y} | {inr(gross)}{raise_text} | {case} |")
    lines += ["", "Unpaid leave (Loss of Pay, approved): KS003 on **21–22 Oct** (before November's cycle, already cut by the old",
              "payroll in October) and **27–28 Oct** (inside November's cycle); KS004 **half a day on 10 Nov** (First Half).", "",
              "Opening balances (April to October): " + "; ".join(f"{c} {inr(g)} paid, {inr(t)} TDS" for c, g, t in openings) + ".", ""]
    titles = (("groups", "the pay groups (the setup above)"), ("shifted", "everyone on the shifted cycle, day 26"),
              ("attendance_cutoff", "everyone on the attendance cutoff, day 26"), ("calendar", "everyone on the calendar month"))
    for name, title in titles:
        r = results[name]
        lines += [f"## Expected November 2026: {title}", "",
                  "| Code | Pay group | Paid for | Unpaid days counted | Pay date | Days | Not employed | Unpaid | Salary for the period | Unpaid cut | Gross | PF | ESI | PT | TDS | Net |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for e in r["employees"]:
            lines.append(f"| {e['code']} | {e['pay_group']} | {e['paid_for']} | {e['unpaid_counted']} | {date.fromisoformat(e['pay_date']):%-d %b} | "
                         f"{e['days_in_month']} | {e['not_employed_days']} | {e['unpaid_days']} | {inr(e['fixed_gross'])} | "
                         f"{inr(e['lop_deduction'])} | {inr(e['gross'])} | {inr(e['pf_employee'])} | {inr(e['esi_employee'])} | "
                         f"{inr(e['pt'])} | {inr(e['tds'])} | **{inr(e['net_pay'])}** |")
        tds = r["dues"]["tds"]
        lines += ["", "TDS to deposit: " + "; ".join(f"{inr(x['total'])} by {date.fromisoformat(x['due']):%-d %b %Y}"
                                                    + (f" (deducted {Period.parse(m).short_label})" if m else "") for m, x in tds.items())
                  + ". PF and ESI by 15 Dec 2026.", ""]
    lines += ["## What to look for", "",
              "* **Groups in one month:** the register lists *Management* (1–30 Nov, paid 1 Dec) and *Shift staff*",
              "  (26 Oct – 25 Nov, paid 28 Nov), each with a subtotal, and there is a net pay file per group.",
              "* **TDS split by pay date:** Shift staff are paid on 28 Nov, so their TDS is deducted in November (due 7 Dec);",
              "  KS001 is paid on 1 Dec (due 7 Jan).",
              "* **KS003:** the shifted cycle cuts the 2 days of 27–28 Oct; the calendar month cuts nothing in November.",
              "  21–22 Oct is never in November.",
              "* **KS004** (joined 28 Oct): shifted pays from 28 Oct, so 2 days (26–27 Oct) are not employed; and **0.5** unpaid",
              "  day for the half day on 10 Nov (Horilla's First Half).",
              "* **KS005** (joined 17 Nov): shifted pays 17–25 Nov (9 of 31 days); the calendar month 17–30 Nov (14 of 30 days).",
              "* **KS002:** TDS starts this month: the raise takes the year's pay over ₹12 lakh after the standard deduction.",
              "* **KS001:** TDS as in every month, adjusted for what was deducted from April to October.",
              "* **KS005** pays ESI this month: with the default setting, ESI cover is decided on this month's gross, which is",
              "  under ₹21,000 because she joined late. Change it on Rules & settings (ESI wage) to decide on the monthly salary instead.",
              "* **Changing a cycle later:** a change from December makes December a transition period for the people it moves",
              "  (from the day after their last paid day to the end of the new cycle, paid for the days it covers).",
              ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
