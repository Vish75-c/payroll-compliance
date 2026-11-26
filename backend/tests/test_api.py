"""The screens' API: sign-in, roles, and a month from opening to final through HTTP (local workflow)."""
import pytest
from django.test import Client

from payroll.demo import setup

from .conftest import REPO

pytestmark = pytest.mark.django_db


def login(username):
    c = Client()
    assert c.post("/api/login", {"username": username, "password": f"{username}123"}, content_type="application/json").status_code == 200
    return c


def test_month_through_the_api(demo):
    company, _ = demo
    for m in ("2025-04", "2025-05", "2025-06"):
        setup.run_month(company, m, log=lambda *a: None)
    assert Client().get(f"/api/companies/{company.pk}/overview").status_code in (401, 403)
    maker, checker = login("maker"), login("checker")
    # The only reviewed HR data was read before June was approved: July must not open with it.
    stale = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-07"}, content_type="application/json")
    assert stale.status_code == 400 and stale.json()["code"] == "stale_data"
    setup.import_spreadsheet(company, REPO / "data/brickline/brickline_hr_data.xlsx", log=lambda *a: None)  # HR data read again
    run = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-07"}, content_type="application/json").json()
    rid = run["id"]
    assert maker.post(f"/api/runs/{rid}/calculate", content_type="application/json").status_code == 400  # data not reviewed yet
    maker.post(f"/api/runs/{rid}/review-data", content_type="application/json")
    ch = maker.post(f"/api/runs/{rid}/instructions", {"text": "Give Priya a ₹10,000 one-time bonus this month"}, content_type="application/json").json()
    assert ch["proposal"]["items"][0]["amount"] == "10000"
    maker.post(f"/api/changes/{ch['id']}/confirm", content_type="application/json")
    out = maker.post(f"/api/runs/{rid}/calculate", content_type="application/json").json()
    assert out["run"]["status"] == "calculated"
    detail = maker.get(f"/api/runs/{rid}").json()
    priya = next(e for e in detail["comparison"]["employees"] if e["code"] == "BL001")
    assert {d["kind"] for d in priya["drivers"]} >= {"salary", "arrears", "one_time"}
    assert maker.post(f"/api/runs/{rid}/submit", content_type="application/json").status_code == 200
    assert maker.post(f"/api/runs/{rid}/approve", content_type="application/json").status_code == 403
    assert checker.post(f"/api/runs/{rid}/reject", {"note": ""}, content_type="application/json").status_code == 400
    out = checker.post(f"/api/runs/{rid}/approve", {"note": "ok"}, content_type="application/json").json()
    assert out["run"]["status"] == "finalized"
    files = out["run"]["files"]["files"]
    assert set(files) == {"register.csv", "pf.csv", "esi.csv", "professional_tax.csv", "tds.csv"}
    resp = checker.get(f"/api/runs/{rid}/files/pf.csv")
    assert resp.status_code == 200 and b"Employee share" in b"".join(resp.streaming_content)
    assert maker.post(f"/api/runs/{rid}/explain", content_type="application/json").json()["provider"] == "rule-based"
    policies = maker.get(f"/api/companies/{company.pk}/policies").json()
    assert any(g["key"] == "professional_tax" and g["jurisdiction"] == "IN-KA" for g in policies["groups"])


def test_screens_must_be_on_the_hr_tool_itself(demo):
    from payroll.models import Connector

    company, _ = demo
    conn = Connector.objects.create(company=company, name="Demo HRMS", kind="browser", system_name="Demo HRMS",
                                    base_url="http://localhost:8100/", username="hr.admin", password="demo123")
    maker = login("maker")
    other_site = {"employees": {"url": "http://localhost:8000/employee/employee-view/"}, "leave": {"url": "/time-off"}}
    r = maker.post(f"/api/connectors/{conn.pk}/confirm-screens", {"datasets": other_site}, content_type="application/json")
    assert r.status_code == 400 and "localhost:8000" in r.json()["detail"]
    conn.refresh_from_db()
    assert conn.recipe_confirmed_at is None
    same_site = {"employees": {"url": "/people"}, "leave": {"url": "http://localhost:8100/time-off"}}
    r = maker.post(f"/api/connectors/{conn.pk}/confirm-screens", {"datasets": same_site}, content_type="application/json")
    assert r.status_code == 200


def _sheet_file(sheet, as_csv=False):
    """One sheet of the Brickline workbook as its own file, the way a company keeps each list separately."""
    import csv
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from openpyxl import Workbook, load_workbook

    from .conftest import REPO

    rows = list(load_workbook(REPO / "data/brickline/brickline_hr_data.xlsx")[sheet].iter_rows(values_only=True))
    buf = io.StringIO() if as_csv else io.BytesIO()
    if as_csv:
        csv.writer(buf).writerows([[v.date().isoformat() if hasattr(v, "date") else v for v in r] for r in rows])
        return SimpleUploadedFile(f"{sheet.lower()}_july.csv", buf.getvalue().encode())
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    wb.save(buf)
    return SimpleUploadedFile(f"{sheet.lower()}_list.xlsx", buf.getvalue())


def _content(snapshot_id):
    from payroll.models import DataSnapshot
    from payroll.features.hr_data.snapshots import content_of

    return content_of(DataSnapshot.objects.get(pk=snapshot_id).data)


def test_employees_salary_and_leave_can_be_separate_files(demo):
    from payroll.features.hr_data.snapshots import content_of

    company, workbook_snapshot = demo
    maker = login("maker")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {
        "employees": _sheet_file("Employees"), "compensation": _sheet_file("Salary", as_csv=True), "leave": _sheet_file("Leave"),
    }).json()
    assert {t["name"]: t["dataset_hint"] for t in job["tables"]} == {"Employees": "employees", "Salary": "compensation", "Leave": "leave"}
    assert [f["dataset"] for f in job["stats"]["files"]] == ["employees", "compensation", "leave"]
    assert job["status"] == "ready"  # same columns as the workbook: the confirmed mappings are reused
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    assert _content(snap["id"]) == content_of(workbook_snapshot.data)


def test_a_file_can_be_added_to_a_read_before_the_snapshot(demo):
    from payroll.features.hr_data.snapshots import content_of

    company, workbook_snapshot = demo
    maker, checker = login("maker"), login("checker")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {"employees": _sheet_file("Employees"), "compensation": _sheet_file("Salary")}).json()
    assert len(job["tables"]) == 2
    assert checker.post(f"/api/jobs/{job['id']}/files", {"leave": _sheet_file("Leave")}).status_code == 403
    job = maker.post(f"/api/jobs/{job['id']}/files", {"leave": _sheet_file("Leave")}).json()
    assert sorted(t["name"] for t in job["tables"]) == ["Employees", "Leave", "Salary"]
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    assert _content(snap["id"]) == content_of(workbook_snapshot.data)
    assert not any(c["code"] == "no_leave_data" for c in snap["checks"])
    late = maker.post(f"/api/jobs/{job['id']}/files", {"leave": _sheet_file("Leave")})
    assert late.status_code == 400 and "already built" in late.json()["detail"]


def test_missing_or_misplaced_files_are_flagged(demo):
    company, _ = demo
    maker = login("maker")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {"employees": _sheet_file("Employees"), "compensation": _sheet_file("Leave")}).json()
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    codes = {c["code"] for c in snap["checks"] if c["level"] in ("warning", "error")}
    assert {"wrong_kind_of_data", "no_salary"} <= codes
    job = maker.post(f"/api/companies/{company.pk}/uploads", {"employees": _sheet_file("Employees"), "compensation": _sheet_file("Salary")}).json()
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    assert "no_leave_data" in {c["code"] for c in snap["checks"]}
    assert maker.post(f"/api/companies/{company.pk}/uploads", {"payslips": _sheet_file("Salary")}).status_code == 400


def test_the_same_file_is_not_added_twice_and_another_layout_is_not_a_changed_screen(demo):
    from openpyxl import Workbook
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile

    company, _ = demo
    maker = login("maker")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {"employees": _sheet_file("Employees"), "compensation": _sheet_file("Salary")}).json()
    # Exported again: different bytes (a workbook records when it was saved), the same rows. Not added twice.
    again = maker.post(f"/api/jobs/{job['id']}/files", {"compensation": _sheet_file("Salary", as_csv=True)})
    assert again.status_code == 400 and "already in this read" in again.json()["detail"]
    # A leave export laid out nothing like the workbook's "Leave" sheet just needs its own mapping.
    wb = Workbook()
    wb.active.append(["Employee", "Start Date", "End Date", "Requested Days", "Status"])
    wb.active.append(["Arjun Mehta (BL002)", "2025-07-14", "2025-07-16", "3", "Approved"])
    buf = io.BytesIO()
    wb.save(buf)
    job = maker.post(f"/api/jobs/{job['id']}/files", {"leave": SimpleUploadedFile("Leave_requests.xlsx", buf.getvalue())}).json()
    leave = next(t for t in job["tables"] if t["name"] == "Leave")
    assert leave["mapping"] is None and not leave["read_info"].get("issues")


def test_a_column_that_is_zero_on_every_row_is_not_suggested(demo):
    from openpyxl import Workbook
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile

    company, _ = demo
    wb = Workbook()
    wb.active.append(["Badge ID", "First Name", "Basic Salary", "Date Joining"])
    wb.active.append(["BL001", "Priya", 0, "2021-06-14"])
    wb.active.append(["BL002", "Arjun", 0, "2024-02-01"])
    buf = io.BytesIO()
    wb.save(buf)
    maker = login("maker")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {"employees": SimpleUploadedFile("employee_export.xlsx", buf.getvalue())}).json()
    job = maker.post(f"/api/jobs/{job['id']}/suggest-mapping").json()
    mapping = job["tables"][0]["mapping"]["mapping"]
    assert mapping["Basic Salary"] == "ignore" and mapping["Badge ID"] == "employee.code"


def test_hr_adds_an_unauthorised_absence_by_hand(demo):
    """Absent without approval: no HR tool has it as leave, so HR adds it on the month, on top of approved leave."""
    from payroll.models import RunEvent

    company, _ = demo
    for m in ("2025-04", "2025-05", "2025-06"):
        setup.run_month(company, m, log=lambda *a: None)
    setup.import_spreadsheet(company, REPO / "data/brickline/brickline_hr_data.xlsx", log=lambda *a: None)  # HR data read again after June was approved
    maker, checker = login("maker"), login("checker")
    rid = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-07"}, content_type="application/json").json()["id"]
    maker.post(f"/api/runs/{rid}/review-data", content_type="application/json")
    url = f"/api/runs/{rid}/add-by-hand"
    absent = {"kind": "extra_unpaid_days", "employee_code": "BL002", "days": "2", "from": "2025-07-21", "to": "2025-07-22",
              "reason": "Absent without approval"}

    assert checker.post(url, absent, content_type="application/json").status_code == 403
    assert "Say why" in maker.post(url, {**absent, "reason": ""}, content_type="application/json").json()["detail"]
    assert "within July 2025" in maker.post(url, {**absent, "from": "2025-08-01", "to": "2025-08-02"}, content_type="application/json").json()["detail"]
    assert "more than the 31 days" in maker.post(url, {**absent, "days": "30", "from": "", "to": ""}, content_type="application/json").json()["detail"]

    ch = maker.post(url, absent, content_type="application/json").json()
    assert ch["status"] == "confirmed" and ch["proposal"]["entered_by_hand"]
    assert "On top of the 3 unpaid days already counted" in ch["proposal"]["items"][0]["summary"]  # 3 days of approved Loss of Pay
    bonus = maker.post(url, {"kind": "one_time_earning", "employee_code": "BL003", "item": "bonus", "amount": "5000"},
                       content_type="application/json").json()
    assert bonus["proposal"]["items"][0]["amount"] == "5000"

    out = maker.post(f"/api/runs/{rid}/calculate", content_type="application/json").json()
    emps = {e["code"]: e for e in out["run"]["result"]["employees"]} if "result" in out["run"] else None
    if emps is None:
        emps = {e["code"]: e for e in maker.get(f"/api/runs/{rid}").json()["result"]["employees"]}
    assert emps["BL002"]["amounts"]["unpaid_days"] == "5"   # 3 approved Loss of Pay + 2 absent without approval
    assert emps["BL003"]["amounts"]["gross"] == "35000"     # 30,000 salary + 5,000 bonus
    assert RunEvent.objects.filter(run_id=rid, action="change_added_by_hand").count() == 2


def _approve(maker, checker, rid):
    maker.post(f"/api/runs/{rid}/review-data", content_type="application/json")
    assert maker.post(f"/api/runs/{rid}/calculate", content_type="application/json").status_code == 200
    assert maker.post(f"/api/runs/{rid}/submit", content_type="application/json").status_code == 200
    assert checker.post(f"/api/runs/{rid}/approve", {"note": "ok"}, content_type="application/json").json()["run"]["status"] == "finalized"


def _amounts(maker, rid, code):
    return next(e for e in maker.get(f"/api/runs/{rid}").json()["result"]["employees"] if e["code"] == code)["amounts"]


def test_salary_change_by_hand_pays_arrears_once_and_carries_on(demo):
    company, _ = demo
    for m in ("2025-04", "2025-05", "2025-06"):
        setup.run_month(company, m, log=lambda *a: None)
    setup.import_spreadsheet(company, REPO / "data/brickline/brickline_hr_data.xlsx", log=lambda *a: None)  # HR data read again after June was approved
    maker, checker = login("maker"), login("checker")
    july = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-07"}, content_type="application/json").json()["id"]
    url = f"/api/runs/{july}/add-by-hand"
    raise_ = {"kind": "salary_revision", "employee_code": "BL003", "amount": "36000", "effective_from": "2025-05-01", "reason": "Promotion"}
    assert "after July 2025" in maker.post(url, {**raise_, "effective_from": "2025-08-01"}, content_type="application/json").json()["detail"]
    ch = maker.post(url, raise_, content_type="application/json").json()
    assert "May 2025 and June 2025 are already paid" in ch["proposal"]["items"][0]["summary"]
    _approve(maker, checker, july)
    a = _amounts(maker, july, "BL003")
    assert a["arrears"] == "12000" and a["fixed_gross"] == "36000"   # 6,000 more for May and for June

    # August: the HR data still says 30,000. The confirmed change keeps counting, and nothing is paid twice or taken back.
    setup.import_spreadsheet(company, REPO / "data/brickline/brickline_hr_data.xlsx", log=lambda *a: None)  # read again after July
    aug = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-08"}, content_type="application/json").json()["id"]
    maker.post(f"/api/runs/{aug}/review-data", content_type="application/json")
    maker.post(f"/api/runs/{aug}/calculate", content_type="application/json")
    a = _amounts(maker, aug, "BL003")
    assert a["fixed_gross"] == "36000" and a.get("arrears", "0") == "0"


def test_unpaid_absence_found_later_is_recovered_once(demo):
    company, _ = demo
    for m in ("2025-04", "2025-05", "2025-06"):
        setup.run_month(company, m, log=lambda *a: None)
    setup.import_spreadsheet(company, REPO / "data/brickline/brickline_hr_data.xlsx", log=lambda *a: None)  # HR data read again after June was approved
    maker, checker = login("maker"), login("checker")
    july = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-07"}, content_type="application/json").json()["id"]
    url = f"/api/runs/{july}/add-by-hand"
    late = {"kind": "past_unpaid_days", "employee_code": "BL006", "month": "2025-06", "days": "2", "from": "2025-06-23",
            "to": "2025-06-24", "reason": "Absent without approval, found in the July attendance review"}
    assert "already paid" in maker.post(url, {**late, "month": "2025-07"}, content_type="application/json").json()["detail"]
    assert "Say why" in maker.post(url, {**late, "reason": ""}, content_type="application/json").json()["detail"]
    ch = maker.post(url, late, content_type="application/json").json()
    assert "recovered in July 2025" in ch["proposal"]["items"][0]["summary"]
    _approve(maker, checker, july)
    a = _amounts(maker, july, "BL006")
    assert a["arrears"] == "-1666"   # 2 of June's 30 days of 25,000, per component: 833 + 333 + 500
    detail = maker.get(f"/api/runs/{july}").json()["result"]["employees"]
    line = next(l for l in next(e for e in detail if e["code"] == "BL006")["lines"] if l["code"] == "arrears")
    assert line["label"] == "Recovery for earlier months" and "2 more unpaid days reported after it was paid" in line["children"][0]["reason"]

    setup.import_spreadsheet(company, REPO / "data/brickline/brickline_hr_data.xlsx", log=lambda *a: None)  # read again after July
    aug = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-08"}, content_type="application/json").json()["id"]
    maker.post(f"/api/runs/{aug}/review-data", content_type="application/json")
    maker.post(f"/api/runs/{aug}/calculate", content_type="application/json")
    assert _amounts(maker, aug, "BL006").get("arrears", "0") == "0"   # not paid back in August


def test_unpaid_days_counted_by_mistake_are_paid_back(demo):
    company, _ = demo
    for m in ("2025-04", "2025-05", "2025-06"):
        setup.run_month(company, m, log=lambda *a: None)
    setup.import_spreadsheet(company, REPO / "data/brickline/brickline_hr_data.xlsx", log=lambda *a: None)  # HR data read again after June was approved
    maker, checker = login("maker"), login("checker")
    july = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-07"}, content_type="application/json").json()["id"]
    url = f"/api/runs/{july}/add-by-hand"
    fix = {"kind": "past_unpaid_days", "direction": "remove", "employee_code": "BL005", "month": "2025-06", "days": "1",
           "reason": "Approved sick leave, counted as unpaid by mistake"}
    assert "cannot be taken off" in maker.post(url, {**fix, "days": "2"}, content_type="application/json").json()["detail"]
    ch = maker.post(url, fix, content_type="application/json").json()
    assert "paid back in July 2025" in ch["proposal"]["items"][0]["summary"]
    _approve(maker, checker, july)
    assert _amounts(maker, july, "BL005")["arrears"] == "700"   # 1 of June's 30 days of 21,000


def test_data_with_warnings_needs_them_read_before_use(demo, settings):
    from openpyxl import Workbook
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile

    company, _ = demo
    wb = Workbook()
    wb.active.append(["Employee ID", "From", "To", "Days", "Status"])  # no leave type: every leave counts as unpaid
    wb.active.append(["BL002", "2025-07-14", "2025-07-14", 1, "Approved"])
    buf = io.BytesIO()
    wb.save(buf)
    maker = login("maker")
    job = maker.post(f"/api/companies/{company.pk}/uploads", {"employees": _sheet_file("Employees"), "compensation": _sheet_file("Salary"),
                                                             "leave": SimpleUploadedFile("leave.xlsx", buf.getvalue())}).json()
    job = maker.post(f"/api/jobs/{job['id']}/suggest-mapping").json()   # the hand-made leave file has no saved mapping yet
    for t in job["tables"]:
        if not t["mapping"]["confirmed"]:
            maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": t["mapping"]["mapping"]}, content_type="application/json")
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    assert any(c["code"] == "leave_without_type" for c in snap["checks"])
    refused = maker.post(f"/api/snapshots/{snap['id']}/approve", {}, content_type="application/json")
    assert refused.status_code == 400 and "I have read the warnings" in refused.json()["detail"]
    ok = maker.post(f"/api/snapshots/{snap['id']}/approve", {"warnings_read": True}, content_type="application/json")
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
