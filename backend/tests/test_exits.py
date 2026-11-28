"""Leaving: a leaving date counts only once the person has left, and why they left becomes the PF return's exit
reason code. The resignation sheet comes in as a second employee file next to the employee list."""
from datetime import date
from decimal import Decimal

import pytest

from payroll.features.hr_data.canonical import heuristic_mapping
from payroll.features.hr_data.normalize import build_snapshot, exit_code

STRUCTURE = {"components": [{"code": "basic", "share": "0.50"}, {"code": "hra", "share": "0.20"},
                            {"code": "special_allowance", "share": "remainder"}]}


def test_the_resignation_template_maps_the_columns_payroll_uses():
    headers = ["Employee Number", "Leaving Date", "Resignation Submission Date", "Reason For Leaving", "Has Left The Organization?",
               "Is Deceased?", "Date Of Death", "Employee Feedback", "Final Settlement Date", "Remarks", "PF Leaving Reason",
               "Separation Mode", "Retirement Date", "Notice Period (in days)"]
    got = {h: v["target"] for h, v in heuristic_mapping(headers, "employees").items() if v["target"] != "ignore"}
    assert got == {"Employee Number": "employee.code", "Leaving Date": "employee.date_of_exit", "Has Left The Organization?": "employee.has_left",
                   "Is Deceased?": "employee.deceased", "PF Leaving Reason": "employee.exit_reason"}


@pytest.mark.parametrize("text, code", [
    ("Resigned", "C"), ("TERMINATED", "C"), ("Contract expiry", "C"), ("Absconding", "C"), ("Retired", "R"), ("Superannuation", "S"),
    ("Death", "D"), ("Expired", "D"), ("Permanent disablement", "P"),
    ("CONTRACT EXPIRY / DEPORTED / EXPIRED / OTHERS / RETIRED / RESIGNED / SICK / TERMINATED", None),  # a list of choices
    ("Death/Resigned etc.", None), ("", None),
])
def test_exit_codes(text, code):
    assert exit_code(text) == code


def _snapshot(exits):
    people = {"name": "Employees", "headers": ["ID", "Name", "Salary", "Joined", "State", "Gender"], "dataset_hint": "employees",
              "rows": [{"ID": f"E{n}", "Name": f"Person {n}", "Salary": "40000", "Joined": "2020-01-01", "State": "Karnataka",
                        "Gender": "Female"} for n in range(1, 6)],
              "mapping": {"ID": "employee.code", "Name": "employee.name", "Salary": "comp.monthly_gross", "Joined": "employee.date_of_joining",
                          "State": "employee.work_state", "Gender": "employee.gender"}}
    resignations = {"name": "Employees (2)", "headers": ["Employee Number", "Leaving Date", "Has Left", "Deceased", "PF Leaving Reason"],
                    "dataset_hint": "employees", "rows": exits,
                    "mapping": {"Employee Number": "employee.code", "Leaving Date": "employee.date_of_exit", "Has Left": "employee.has_left",
                                "Deceased": "employee.deceased", "PF Leaving Reason": "employee.exit_reason"}}
    data, checks = build_snapshot([people, resignations], STRUCTURE, date(2025, 8, 1))
    return {e["code"]: e for e in data["employees"]}, {c["code"]: c for c in checks}


def test_a_leaving_date_counts_once_the_person_has_left_and_gets_a_pf_exit_code():
    people, checks = _snapshot([
        {"Employee Number": "E1", "Leaving Date": "15-Jul-2025", "Has Left": "yes", "Deceased": "no", "PF Leaving Reason": "Resigned"},
        {"Employee Number": "E2", "Leaving Date": "31-Aug-2025", "Has Left": "no", "Deceased": "", "PF Leaving Reason": "Resigned"},
        {"Employee Number": "E3", "Leaving Date": "10-Jul-2025", "Has Left": "yes", "Deceased": "yes", "PF Leaving Reason": "Others"},
        {"Employee Number": "E4", "Leaving Date": "20-Jul-2025", "Has Left": "yes", "Deceased": "no",
         "PF Leaving Reason": "CONTRACT EXPIRY / RETIRED / RESIGNED"},
    ])
    assert (people["E1"]["date_of_exit"], people["E1"]["exit_reason"]) == ("2025-07-15", "C")
    assert people["E1"]["name"] == "Person 1"  # the resignation sheet adds to the employee list; it does not replace it
    assert "date_of_exit" not in people["E2"] and people["E2"]["planned_exit"] == "2025-08-31"  # not left yet: pay continues
    assert "planned, not done" in checks["planned_exit"]["message"]
    assert people["E3"]["exit_reason"] == "D"  # "Is deceased" decides it, whatever the reason column says
    assert "exit_reason" not in people["E4"] and "does not say clearly" in checks["exit_reason_unclear"]["message"]
    assert checks["no_exit_reason"]["detail"]["employees"] == ["E4"]
    assert "exit_reason" not in people["E5"] and "date_of_exit" not in people["E5"]  # still working


@pytest.mark.django_db
def test_a_planned_leaving_date_does_not_cut_the_months_pay(demo):
    from payroll.demo import setup
    from payroll.features.hr_data import snapshots
    from payroll.features.payroll_runs import runs
    from payroll.models import DataSnapshot

    company, snapshot = demo
    for m in ("2025-04", "2025-05", "2025-06"):
        setup.run_month(company, m, log=lambda *a: None)
    maker = setup.system_user("maker")

    def july_gross(change):
        data = dict(snapshot.data)
        data["employees"] = [dict(e, **change) if e["code"] == "BL004" else e for e in data["employees"]]
        s = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet", data=data,
                                        content_hash=f"exit-{sorted(change)}", checks=[])
        snapshots.approve(s, maker)
        run = runs.open_month(company, "2025-07", maker, snapshot=s)
        runs.review_data(run.pk, maker)
        run = runs.calculate(run.pk, maker)
        gross = Decimal(next(e for e in run.result["employees"] if e["code"] == "BL004")["amounts"]["gross"])
        runs.cancel(run.pk, maker)
        return gross

    left = july_gross({"date_of_exit": "2025-07-15", "exit_reason": "C"})
    planned = july_gross({"planned_exit": "2025-07-15"})
    assert planned > left  # planned: a full month; left on the 15th: half the month
