"""A month's one-time inputs from a sheet, from any HR tool: each column gets a treatment, the column's own name is the
payslip label, a person reviews and confirms, and only then does it reach pay. Kinds not supported yet are never
dropped silently."""
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from payroll.demo import setup
from payroll.features.payroll_runs import runs
from payroll.features.payroll_runs.monthly_inputs import suggest
from payroll.models import ChangeRequest, PayrollRun

from .test_api import login
from .test_reading_any_sheet import NOTES, _xlsx

pytestmark = pytest.mark.django_db
JSON = "application/json"
HEAD = ["Employee Number", "Effective From", "Remarks", "PERFORMANCE BONUS", "Diwali Gift", "WFH REIMBURSEMENT", "CANTEEN RECOVERY",
        "VPF", "OT HOURS", "OT RATE", "FULL DA", "LOP Days"]


def _sheet(rows):
    return SimpleUploadedFile("Bulk Salary Information.xlsx", _xlsx([HEAD, *rows, *NOTES], declared_size="A1"))


def _july(company):
    for m in ("2025-04", "2025-05", "2025-06"):
        setup.run_month(company, m, log=lambda *a: None)
    maker = setup.system_user("maker")
    july = runs.open_month(company, "2025-07", maker)
    runs.review_data(july.pk, maker)
    return july


ROWS = [["BL001", "01-Jul-2025", "", "25000", "5000", "1500", "800", "2000", "", "", "", ""],
        ["BL002", "01-Jul-2025", "late punch", "", "", "", "", "", "10", "300", "", "2"]]


def test_any_tools_columns_get_a_treatment():
    got = {h: v["treatment"] for h, v in suggest(HEAD + ["DIRECT TDS", "GRATUITY", "ESOP PERQUISITES", "Night Shift Pay", "Mystery Col"],
                                                [{"Mystery Col": "500"}]).items()}
    assert got == {"Employee Number": "employee_code", "Effective From": "month", "Remarks": "ignore", "PERFORMANCE BONUS": "taxable_earning",
                   "Diwali Gift": "taxable_earning", "WFH REIMBURSEMENT": "reimbursement", "CANTEEN RECOVERY": "deduction", "VPF": "vpf",
                   "OT HOURS": "overtime_hours", "OT RATE": "overtime_rate", "FULL DA": "salary_rate", "LOP Days": "unpaid_days",
                   "DIRECT TDS": "tds_override", "GRATUITY": "gratuity", "ESOP PERQUISITES": "perquisite", "Night Shift Pay": "taxable_earning",
                   "Mystery Col": "taxable_earning"}
    assert suggest(["Mystery Col"], [{"Mystery Col": "500"}])["Mystery Col"]["sure"] is False  # amounts, unknown name: a person checks


def test_a_months_inputs_from_upload_to_pay(demo):
    company, _ = demo
    july = _july(company)
    maker = login("maker")
    before = {e["code"]: e for e in runs.calculate(july.pk, setup.system_user("maker")).result["employees"]}

    run = maker.post(f"/api/runs/{july.pk}/monthly-inputs", {"file": _sheet(ROWS)}).json()["run"]
    change = next(c for c in run["changes"] if c["proposal"].get("source") == "monthly_inputs")
    p = change["proposal"]
    assert change["status"] == "proposed" and "treatments" in change and len(p["rows"]) == 2  # the note lines were not read
    assert {i["employee_code"] for i in p["items"]} == {"BL001", "BL002"}
    assert p["problems"] == []  # the FULL DA column is empty, so nothing is lost
    from payroll.shared.permissions import RuleViolation

    with pytest.raises(RuleViolation, match="proposed change"):  # nothing goes to the checker with inputs not reviewed
        runs.check_submit(PayrollRun.objects.get(pk=july.pk), setup.system_user("maker"))

    assert maker.post(f"/api/changes/{change['id']}/confirm", content_type=JSON).status_code == 200
    july = runs.calculate(july.pk, setup.system_user("maker"))
    after = {e["code"]: e for e in july.result["employees"]}
    priya, arjun = after["BL001"], after["BL002"]
    lines = {l["code"]: l for l in priya["lines"]}
    assert lines["one_time.performance_bonus"]["label"] == "Performance Bonus" and Decimal(lines["one_time.performance_bonus"]["amount"]) == 25000
    assert lines["one_time.diwali_gift"]["label"] == "Diwali Gift"
    assert "not taxable" in lines["one_time.wfh_reimbursement"]["reason"]
    assert Decimal(priya["amounts"]["deduction.canteen_recovery"]) == 800
    # Voluntary PF goes into the person's PF, not a separate deduction.
    assert Decimal(priya["amounts"]["vpf"]) == 2000
    assert Decimal(priya["amounts"]["pf_employee"]) == Decimal(before["BL001"]["amounts"]["pf_employee"]) + 2000
    # Overtime from hours × rate when there is no overtime pay; unpaid days for the month replace the HR data's figure.
    assert Decimal(arjun["amounts"]["one_time.overtime"]) == 3000 and arjun["amounts"]["unpaid_days"] == "2"
    # Gross: 25,000 + 5,000 + 1,500 more for Priya; taxable: only the bonus and the gift.
    assert Decimal(priya["amounts"]["gross"]) - Decimal(before["BL001"]["amounts"]["gross"]) == 31500


def test_kinds_not_supported_yet_are_never_dropped_silently(demo):
    company, _ = demo
    july = _july(company)
    maker = login("maker")
    rows = [ROWS[0][:10] + ["40000", ""]]
    change = next(c for c in maker.post(f"/api/runs/{july.pk}/monthly-inputs", {"file": _sheet(rows)}).json()["run"]["changes"]
                  if c["proposal"].get("source") == "monthly_inputs")
    assert any("'FULL DA' has amounts for 1 person, but a monthly salary rate belongs in the salary file" in x for x in change["proposal"]["problems"])
    assert maker.post(f"/api/changes/{change['id']}/confirm", content_type=JSON).status_code == 400
    # The finance team employee decides to go ahead without it, on purpose:
    columns = {**change["proposal"]["columns"], "FULL DA": "ignore"}
    run = maker.post(f"/api/changes/{change['id']}/columns", {"columns": columns}, content_type=JSON).json()["run"]
    reviewed = next(c for c in run["changes"] if c["id"] == change["id"])
    assert reviewed["proposal"]["problems"] == [] and any("Not used, though they have amounts: FULL DA" in n for n in reviewed["proposal"]["notes"])
    assert maker.post(f"/api/changes/{change['id']}/confirm", content_type=JSON).status_code == 200


def test_safeguards(demo):
    company, _ = demo
    july = _july(company)
    maker, checker = login("maker"), login("checker")
    assert checker.post(f"/api/runs/{july.pk}/monthly-inputs", {"file": _sheet(ROWS)}).status_code == 403
    first = maker.post(f"/api/runs/{july.pk}/monthly-inputs", {"file": _sheet(ROWS)})
    assert first.status_code == 200
    again = maker.post(f"/api/runs/{july.pk}/monthly-inputs", {"file": _sheet(ROWS)})
    assert again.status_code == 400 and "pay every amount twice" in again.json()["detail"]
    # Rows for another month, and a person not in the month's HR data.
    odd = [["BL003", "01-Aug-2025", "", "1000"] + [""] * 8, ["ZZ999", "01-Jul-2025", "", "1000"] + [""] * 8]
    change = next(c for c in maker.post(f"/api/runs/{july.pk}/monthly-inputs", {"file": _sheet(odd)}).json()["run"]["changes"]
                  if c["proposal"].get("file") and c["proposal"]["rows"][0]["Employee Number"] == "BL003")
    problems = " ".join(change["proposal"]["problems"])
    assert "rows are for 2025-08, not July 2025" in problems and "ZZ999" in problems


def test_confirmed_treatments_are_reused_for_the_next_file_with_the_same_columns(demo):
    company, _ = demo
    july = _july(company)
    maker = login("maker")
    change = next(c for c in maker.post(f"/api/runs/{july.pk}/monthly-inputs", {"file": _sheet(ROWS)}).json()["run"]["changes"]
                  if c["proposal"].get("source") == "monthly_inputs")
    columns = {**change["proposal"]["columns"], "Diwali Gift": "reimbursement"}  # the company decides gifts are not taxable here
    maker.post(f"/api/changes/{change['id']}/columns", {"columns": columns}, content_type=JSON)
    maker.post(f"/api/changes/{change['id']}/confirm", content_type=JSON)
    other = [["BL004", "01-Jul-2025", "", "1000", "700"] + [""] * 7]
    run = maker.post(f"/api/runs/{july.pk}/monthly-inputs", {"file": _sheet(other)}).json()["run"]
    second = next(c for c in run["changes"] if c["status"] == "proposed")
    assert second["proposal"]["reused_mapping"] and second["proposal"]["columns"]["Diwali Gift"] == "reimbursement"
    assert ChangeRequest.objects.filter(run=PayrollRun.objects.get(pk=july.pk), status="confirmed").count() == 1
