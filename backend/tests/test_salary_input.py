"""Salary as companies give it: annual CTC with variable pay kept out of monthly pay, other parts of salary under their
own names (LTA, meal coupons, stipend...), HRA on or off per person, and revisions marked "no arrears"."""
from datetime import date
from decimal import Decimal

import pytest

from payroll.features.hr_data.canonical import heuristic_mapping, part_code
from payroll.features.hr_data.normalize import build_snapshot
from payroll.features.payroll_runs.inputs import _known_compensation

from .test_engine import amounts, emp, run

STRUCTURE = {"components": [{"code": "basic", "share": "0.50"}, {"code": "hra", "share": "0.20"},
                            {"code": "special_allowance", "share": "remainder"}]}


def test_the_salary_template_maps_without_guessing():
    headers = ["Employee Number", "Effective Date", "Employee Remarks", "Notes", "FULL CONSULTANCY FEES", "FULL TOTAL CTC",
               "FULL VARIABLE CTC", "FULL SALES COMMISION", "NPS PERCENTAGE", "FULL STIPEND", "FULL BOOKS AND PERIODICAL", "FULL LTA",
               "FULL ICICI MEAL COUPON", "HRA APPLICABLE Yes=1 No=0", "Is Arrear Calculation Required"]
    got = {h: g["target"] for h, g in heuristic_mapping(headers, "compensation").items()}
    assert got == {
        "Employee Number": "employee.code", "Effective Date": "comp.effective_from", "Employee Remarks": "ignore", "Notes": "ignore",
        "FULL CONSULTANCY FEES": "ignore",  # a consultant is not paid as salary
        "FULL TOTAL CTC": "comp.annual_ctc", "FULL VARIABLE CTC": "comp.variable_annual",
        "FULL SALES COMMISION": "comp.part@sales_commision", "NPS PERCENTAGE": "comp.nps_percent", "FULL STIPEND": "comp.part@stipend",
        "FULL BOOKS AND PERIODICAL": "comp.part@books_and_periodical", "FULL LTA": "comp.part@lta",
        "FULL ICICI MEAL COUPON": "comp.part@icici_meal_coupon", "HRA APPLICABLE Yes=1 No=0": "comp.hra_applicable",
        "Is Arrear Calculation Required": "comp.arrears"}


def test_another_tools_parts_get_their_own_names():
    got = {h: g["target"] for h, g in heuristic_mapping(["Emp Code", "Monthly Gross", "Fuel Allowance", "Telephone Reimb", "Remarks"],
                                                        "compensation").items()}
    assert got["Fuel Allowance"] == "comp.part@fuel_allowance" and got["Remarks"] == "ignore"
    assert part_code("Children Education Allowance (Monthly)") == "children_education_allowance"


def _salary(rows, headers):
    mapping = {"ID": "employee.code", "From": "comp.effective_from", "CTC": "comp.annual_ctc", "Variable": "comp.variable_annual",
               "LTA": "comp.part@lta", "Meal": "comp.part@meal_coupon", "HRA?": "comp.hra_applicable", "Gross": "comp.monthly_gross",
               "Arrears?": "comp.arrears"}
    people = [{"name": "People", "headers": ["ID", "Name"], "rows": [{"ID": r["ID"], "Name": r["ID"]} for r in rows],
               "mapping": {"ID": "employee.code", "Name": "employee.name"}, "dataset_hint": "employees"}]
    salary = [{"name": "Salary", "headers": headers, "rows": rows, "mapping": {h: mapping[h] for h in headers}, "dataset_hint": "compensation"}]
    data, checks = build_snapshot(people + salary, STRUCTURE, date(2025, 7, 1))
    return {r["employee_code"]: r for r in data["compensation"]}, {c["code"]: c for c in checks}


def test_annual_ctc_less_variable_pay_and_named_parts_adds_up_to_a_twelfth():
    rows = [{"ID": "E1", "From": "2025-04-01", "CTC": "1200000", "Variable": "120000", "LTA": "60000", "Meal": "26400"}]
    comp, _ = _salary(rows, ["ID", "From", "CTC", "Variable", "LTA", "Meal"])
    parts = {k: Decimal(v) for k, v in comp["E1"]["components"].items()}
    # (12,00,000 − 1,20,000) ÷ 12 = 90,000 a month; LTA 5,000 and meal coupon 2,200 have their own names; 82,800 is split.
    assert parts == {"basic": 41400, "hra": 16560, "special_allowance": 24840, "lta": 5000, "meal_coupon": 2200}
    assert sum(parts.values()) == 90000 and comp["E1"]["variable_annual"] == "120000"


def test_monthly_salary_with_parts_and_variable_pay():
    rows = [{"ID": "E1", "From": "2025-04-01", "Gross": "50000", "LTA": "4000", "Variable": "100000"}]
    comp, checks = _salary(rows, ["ID", "From", "Gross", "LTA", "Variable"])
    parts = {k: Decimal(v) for k, v in comp["E1"]["components"].items()}
    assert parts["lta"] == 4000 and sum(parts.values()) == 50000  # the part is inside the monthly salary
    assert "variable_annual" not in comp["E1"] and "monthly salary, not an annual CTC" in checks["variable_without_ctc"]["message"]


def test_no_hra_puts_its_share_in_the_rest():
    rows = [{"ID": "E1", "From": "2025-04-01", "Gross": "40000", "HRA?": "0"}, {"ID": "E2", "From": "2025-04-01", "Gross": "40000", "HRA?": "1"}]
    comp, _ = _salary(rows, ["ID", "From", "Gross", "HRA?"])
    assert comp["E1"]["components"] == {"basic": "20000", "special_allowance": "20000"}
    assert comp["E2"]["components"] == {"basic": "20000", "hra": "8000", "special_allowance": "12000"}


def test_a_part_of_salary_is_taxable_and_not_pf_wages(statutory_versions):
    plain = emp("A", 150000)   # taxable: over the ₹12 lakh rebate limit for the year
    with_lta = emp("A", 150000)
    with_lta["compensation"] = [{"effective_from": "2025-04-01", "components": {**plain["compensation"][0]["components"], "lta": "10000"}}]
    a, b = amounts(run(plain, "2025-06", statutory_versions)), amounts(run(with_lta, "2025-06", statutory_versions))
    assert b["gross"] - a["gross"] == 10000 and b["pf_employee"] == a["pf_employee"] and b["tds"] > a["tds"]
    lta = next(l for l in run(with_lta, "2025-06", statutory_versions)["lines"] if l["code"] == "earned.lta")
    assert lta["label"] == "Leave travel allowance" and Decimal(lta["amount"]) == 10000  # its own line on the payslip


# --------------------------------------------------------------------------- no arrears for a revision

def test_a_no_arrears_revision_applies_from_the_month_it_was_recorded():
    rows = [{"effective_from": "2025-04-01", "recorded_on": "2025-04-01", "components": {"basic": "1"}},
            {"effective_from": "2025-05-01", "recorded_on": "2025-07-10", "components": {"basic": "2"}, "no_arrears": True},
            {"effective_from": "2025-09-01", "recorded_on": "2025-07-10", "components": {"basic": "3"}, "no_arrears": True}]
    got = [(r["effective_from"], r["components"]["basic"]) for r in _known_compensation(rows, date(2026, 1, 1))]
    assert got == [("2025-04-01", "1"), ("2025-07-01", "2"), ("2025-09-01", "3")]  # a future date is left as it is


@pytest.mark.django_db
def test_a_no_arrears_revision_pays_no_back_pay(demo):
    from payroll.demo import setup
    from payroll.features.hr_data import snapshots
    from payroll.features.payroll_runs import runs
    from payroll.models import DataSnapshot

    company, snapshot = demo
    for m in ("2025-04", "2025-05", "2025-06"):
        setup.run_month(company, m, log=lambda *a: None)
    maker = setup.system_user("maker")
    code = "BL003"
    last = max((r for r in snapshot.data["compensation"] if r["employee_code"] == code), key=lambda r: r["effective_from"] or "")
    raised = {k: str(int(Decimal(v) * 11 // 10)) for k, v in last["components"].items()}

    def july(no_arrears):
        data = dict(snapshot.data)
        row = {"employee_code": code, "effective_from": "2025-05-01", "recorded_on": "2025-07-05", "recorded_on_assumed": False,
               "components": raised, "source": "test", **({"no_arrears": True} if no_arrears else {})}
        data["compensation"] = data["compensation"] + [row]
        s = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet", data=data,
                                        content_hash=f"raise-{no_arrears}", checks=[])
        snapshots.approve(s, maker)
        run = runs.open_month(company, "2025-07", maker, snapshot=s)
        runs.review_data(run.pk, maker)
        run = runs.calculate(run.pk, maker)
        person = next(e for e in run.result["employees"] if e["code"] == code)
        runs.cancel(run.pk, maker)
        return person

    back_pay, none = july(False), july(True)
    assert Decimal(back_pay["amounts"]["arrears"]) > 0                    # May and June paid again at the new salary
    assert Decimal(none["amounts"].get("arrears", "0")) == 0              # no back pay
    assert none["amounts"]["regular_gross"] == back_pay["amounts"]["regular_gross"]  # July itself is at the new salary
