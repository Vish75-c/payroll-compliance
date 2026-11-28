"""The whole on-hold journey through the API, as the finance team employee and the finance head do it (local mode, no
Temporal): HR data where one person has no work location, a month paid for everyone else, the next month held back
until HR adds it, then that month paying the held salary once."""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook, load_workbook

from payroll.demo import setup
from payroll.models import PayrollRun, RunEmployee

from .conftest import REPO
from .test_api import _sheet_file, login

pytestmark = pytest.mark.django_db
JSON = "application/json"
PERSON = "BL001"  # Priya Sharma


def _employees_without_location(code):
    rows = list(load_workbook(REPO / "data/brickline/brickline_hr_data.xlsx")["Employees"].iter_rows(values_only=True))
    col = rows[0].index("Work Location")
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Employees"
    for r in rows:
        sheet.append([None if (r[0] == code and i == col) else v for i, v in enumerate(r)])
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile("employees_july.xlsx", buf.getvalue())


def _read_hr_data(maker, company, employees):
    job = maker.post(f"/api/companies/{company.pk}/uploads", {"employees": employees, "compensation": _sheet_file("Salary"),
                                                              "leave": _sheet_file("Leave")}).json()
    job = maker.post(f"/api/jobs/{job['id']}/suggest-mapping").json()
    for t in job["tables"]:
        if not t["mapping"]["confirmed"]:
            r = maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": t["mapping"]["mapping"],
                                                                     "value_maps": t["mapping"]["value_maps"]}, content_type=JSON)
            assert r.status_code == 200, r.content
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    assert not [c for c in snap["checks"] if c["level"] == "error"], snap["checks"]
    assert maker.post(f"/api/snapshots/{snap['id']}/approve", {"warnings_read": True}, content_type=JSON).status_code == 200
    return snap


def _act(client, rid, action, **body):
    return client.post(f"/api/runs/{rid}/{action}", body, content_type=JSON)


def _person(run, code):
    return next(e for e in run["result"]["employees"] if e["code"] == code)


def test_one_person_on_hold_from_hr_data_to_the_held_salary_paid(demo):
    company, _ = demo
    for m in ("2025-04", "2025-05"):
        setup.run_month(company, m, log=lambda *a: None)
    maker, checker = login("maker"), login("checker")

    # 1. HR data with Priya's work location missing: a warning naming her, not an error. The data can be used.
    snap = _read_hr_data(maker, company, _employees_without_location(PERSON))
    gap = next(c for c in snap["checks"] if c["code"] == "no_work_state")
    assert gap["level"] == "warning" and PERSON in gap["message"] and "everyone else is paid" in gap["message"]

    # 2. June: Priya waits, everyone else is calculated; submitting says why it cannot go yet.
    june = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-06"}, content_type=JSON).json()
    rid = june["id"]
    assert june["employee_states"]["counts"]["waiting"] == 1
    assert june["employee_states"]["people"][0]["code"] == PERSON and june["employee_states"]["people"][0]["missing_label"] == "work state"
    _act(maker, rid, "review-data")
    run = _act(maker, rid, "calculate").json()["run"]
    assert PERSON not in {e["code"] for e in run["result"]["employees"]} and run["result"]["employee_count"] > 50
    assert run["can"]["submit"] is False
    refused = _act(maker, rid, "submit")
    assert refused.status_code == 400 and "waiting for HR data" in refused.json()["detail"]

    # 3. The finance team employee puts her on hold; June goes ahead for everyone else.
    run = maker.post(f"/api/runs/{rid}/hold", {"employee_code": PERSON, "reason": "HR is confirming her work location"},
                     content_type=JSON).json()["run"]
    assert run["can"]["submit"] is True and run["employee_states"]["people"][0]["status"] == "on_hold"
    assert _act(maker, rid, "submit").status_code == 200
    out = _act(checker, rid, "approve", note="ok").json()
    assert out["run"]["status"] == "finalized"
    register = b"".join(checker.get(f"/api/runs/{rid}/files/register.csv").streaming_content).decode()
    assert PERSON not in register  # not paid in June: not in the register or the bank/PF files

    # 4. July cannot start while Priya's data is still missing.
    overview = maker.get(f"/api/companies/{company.pk}/overview").json()
    assert PERSON in overview["next_blocked"] and "work state" in overview["next_blocked"]
    blocked = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-07"}, content_type=JSON)
    assert blocked.status_code == 400 and "once everyone on hold has their HR data" in blocked.json()["detail"]

    # 5. HR adds it; the data is read and reviewed again; July opens and pays June's held salary.
    complete = _read_hr_data(maker, company, _sheet_file("Employees"))
    assert not [c for c in complete["checks"] if c["code"] == "no_work_state"]
    assert maker.get(f"/api/companies/{company.pk}/overview").json()["next_blocked"] == ""
    july = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-07"}, content_type=JSON).json()
    jid = july["id"]
    assert july["employee_states"]["counts"]["waiting"] == 0
    _act(maker, jid, "review-data")
    run = _act(maker, jid, "calculate").json()["run"]
    priya = _person(run, PERSON)
    arrears = next(line for line in priya["lines"] if line["code"] == "arrears")
    held_line = next(c for c in arrears["children"] if c["code"] == "arrears.2025-06")
    assert held_line["label"] == "Salary for June 2025 (was on hold)" and int(held_line["amount"]) > 0
    assert "HR is confirming her work location" in held_line["reason"]
    assert _act(maker, jid, "submit").status_code == 200
    assert _act(checker, jid, "approve", note="ok").json()["run"]["status"] == "finalized"

    # 6. June's page shows her salary as paid later, in July; it is never paid again.
    june_page = checker.get(f"/api/runs/{rid}").json()["employee_states"]["people"]
    assert june_page == [{**june_page[0], "code": PERSON, "status": "paid_later", "paid_in": "July 2025"}]
    assert RunEmployee.objects.get(run_id=rid, employee_code=PERSON).paid_in_run_id == jid
    assert RunEmployee.objects.filter(run_id=jid, status=RunEmployee.PAID).count() == run["result"]["employee_count"]
    _read_hr_data(maker, company, _sheet_file("Employees"))  # each month needs HR data read after the one before is final
    august = PayrollRun.objects.get(pk=maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-08"},
                                                  content_type=JSON).json()["id"])
    _act(maker, august.pk, "review-data")
    run = _act(maker, august.pk, "calculate").json()["run"]
    assert not [a for a in _person(run, PERSON)["facts"]["arrears_detail"] if a["period"] == "2025-06"]
