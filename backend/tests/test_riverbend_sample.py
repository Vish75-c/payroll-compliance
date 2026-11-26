"""The Riverbend sample (data/riverbend/) through the app, compared with the numbers worked out on their own.

People joining and leaving mid-month: the four HR files are uploaded, November 2026 is paid, a (made-up) government
change raises the PF wage ceiling to 21,000 from 1 December, and December 2026 is paid. Every person's pay, PF, ESI,
professional tax, TDS, net pay and cost to company must match data/riverbend/expected.json, written by
scripts/make_riverbend_sample.py without using the engine.
"""
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from .conftest import REPO
from .test_api import login

pytestmark = pytest.mark.django_db
JSON = "application/json"
DATA = REPO / "data" / "riverbend"
# expected.json field -> the engine's amount
FIELDS = {"days_in_month": "days_in_month", "not_employed_days": "not_employed_days", "unpaid_leave_days": "unpaid_days",
          "gross": "gross", "pf": "pf_employee", "esi": "esi_employee", "pt": "pt", "tds": "tds", "net_pay": "net_pay",
          "pf_company": "pf_employer", "esi_company": "esi_employer", "cost_to_company": "employer_cost",
          "tds_projected_income": "tds_projected_income", "tds_annual_tax": "tds_annual_tax", "tds_already_deducted": "tds_paid_before"}


def _file(name):
    return SimpleUploadedFile(name, (DATA / name).read_bytes())


def _same(engine_value, expected):
    return float(engine_value) == float(expected)


def _check_month(result, expected):
    people = {e["code"]: e["amounts"] for e in result["employees"]}
    assert sorted(people) == sorted(expected), "different people paid"
    wrong = [f"{code} {field}: app {people[code].get(key)} expected {want[field]}"
             for code, want in expected.items() for field, key in FIELDS.items()
             if not _same(people[code].get(key, "0"), want[field])]
    assert not wrong, "\n".join(wrong[:20])


def _pay_month(maker, checker, cid, period, knowledge_date):
    opened = maker.post(f"/api/companies/{cid}/runs", {"period": period, "knowledge_date": knowledge_date}, content_type=JSON)
    assert opened.status_code == 201, opened.content
    run = opened.json()
    assert maker.post(f"/api/runs/{run['id']}/review-data", content_type=JSON).status_code == 200
    out = maker.post(f"/api/runs/{run['id']}/calculate", content_type=JSON)
    assert out.status_code == 200, out.content
    return run["id"]


def _upload_and_review(maker, cid):
    job = maker.post(f"/api/companies/{cid}/uploads", {
        "employees": _file("1_Employees.xlsx"), "compensation": _file("2_Salary.xlsx"),
        "leave": _file("3_Leave.xlsx"), "attendance": _file("4_Attendance.xlsx")}).json()
    job = maker.post(f"/api/jobs/{job['id']}/suggest-mapping").json()
    for t in job["tables"]:
        mapping = t["mapping"]["mapping"]
        assert mapping.get("Remarks", "ignore") == "ignore", f"{t['name']}: Remarks should not be read"
        if not t["mapping"]["confirmed"]:
            body = {"mapping": mapping}
            if t["mapping"].get("value_maps"):
                body["value_maps"] = t["mapping"]["value_maps"]
            assert maker.post(f"/api/tables/{t['id']}/confirm-mapping", body, content_type=JSON).status_code == 200
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    assert snap["employee_count"] == 80
    assert maker.post(f"/api/snapshots/{snap['id']}/approve", {"warnings_read": True}, content_type=JSON).status_code == 200


def test_the_riverbend_sample_gives_the_expected_numbers(db):
    from payroll.demo import setup
    from payroll.models import PayrollRun

    setup.ensure_users()
    setup.load_statutory_rules()
    expected = json.loads((DATA / "expected.json").read_text())
    maker, checker = login("maker"), login("checker")

    co = maker.post("/api/companies", {"name": "Riverbend Interiors Pvt Ltd", "state": "Karnataka", "city": "Bengaluru"},
                    content_type=JSON).json()
    cid = co["id"]
    _upload_and_review(maker, cid)

    opening = maker.post(f"/api/companies/{cid}/opening-balances",
                         {"period": "2026-11", "file": _file("opening_balances.csv")}).json()
    assert opening["count"] == 75 and [m["employee_code"] for m in opening["missing"]] == ["RB019"]

    nov = _pay_month(maker, checker, cid, "2026-11", "2026-11-30")
    november = PayrollRun.objects.get(pk=nov)
    _check_month(november.result, expected["2026-11"])
    absences = maker.get(f"/api/runs/{nov}/possible-absences").json()
    assert [(a["employee_code"], a["dates"]) for a in absences["absences"]] == [("RB022", ["2026-11-18"])]
    assert maker.post(f"/api/runs/{nov}/submit", content_type=JSON).status_code == 200
    assert checker.post(f"/api/runs/{nov}/approve", {"note": "ok"}, content_type=JSON).json()["run"]["status"] == "finalized"

    # The government change, from 1 December: PF wage ceiling 15,000 -> 21,000 (pension ceiling unchanged)
    pf = next(v for v in setup_versions("pf"))
    payload = dict(pf, wage_ceiling="21000")
    added = maker.post(f"/api/companies/{cid}/policies", {"key": "pf", "jurisdiction": "IN", "effective_from": "2026-12-01",
                                                         "payload": payload, "title": "PF wage ceiling ₹21,000 (demo)"},
                       content_type=JSON)
    assert added.status_code == 201, added.content
    # November is not touched: a draft re-run gives the same result
    draft = maker.post(f"/api/runs/{nov}/draft-rerun", {}, content_type=JSON).json()
    assert maker.post(f"/api/runs/{draft['id']}/calculate", content_type=JSON).status_code == 200
    assert PayrollRun.objects.get(pk=draft["id"]).result_hash == november.result_hash

    # December 2026, under the new ceiling. The app pays a month only on HR data read
    # after the previous month was approved, so the files are uploaded again (the same files: nothing changed).
    _upload_and_review(maker, cid)
    dec = _pay_month(maker, checker, cid, "2026-12", "2026-12-31")
    _check_month(PayrollRun.objects.get(pk=dec).result, expected["2026-12"])
    absences = maker.get(f"/api/runs/{dec}/possible-absences").json()
    assert [(a["employee_code"], a["dates"]) for a in absences["absences"]] == [("RB023", ["2026-12-02", "2026-12-03"])]


def setup_versions(key):
    data = json.loads((REPO / "data/rules/statutory_in.json").read_text())["versions"]
    return [v["payload"] for v in data if v["key"] == key]
