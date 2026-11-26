"""Government dues after a final month, the day count set per month, opening balances, and adding a company."""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from payroll.models import PayrollRun, RunEvent, StatutoryDue
from payroll.demo import setup
from payroll.features.dues import dues

from .conftest import REPO
from .test_api import _sheet_file, login

pytestmark = pytest.mark.django_db
JSON = "application/json"


def _round(x: Decimal) -> Decimal:
    return x.quantize(Decimal(1), rounding=ROUND_HALF_UP)


def _sum(result, key, state=None):
    return sum((Decimal(e["amounts"].get(key, "0")) for e in result["employees"]
                if state is None or e.get("work_state") == state), Decimal(0))


# --------------------------------------------------------------------------- government dues

def test_a_final_month_lists_what_to_pay_the_government_and_by_when(demo):
    company, _ = demo
    april = setup.run_month(company, "2025-04", log=lambda *a: None)
    owed = {(x.kind, x.jurisdiction): x for x in april.dues.all()}
    res = april.result
    assert owed[("tds", "")].total == _sum(res, "tds") and owed[("tds", "")].due_date == date(2025, 5, 7)
    assert owed[("pf", "")].employee_amount == _sum(res, "pf_employee")
    assert owed[("pf", "")].employer_amount == _sum(res, "pf_employer") == _sum(res, "eps_employer") + _sum(res, "epf_employer")
    assert owed[("pf", "")].due_date == date(2025, 5, 15)
    assert owed[("esi", "")].total == _sum(res, "esi_employee") + _sum(res, "esi_employer")
    states = {e["work_state"] for e in res["employees"] if Decimal(e["amounts"].get("pt", "0"))}
    for st in states:
        assert owed[("pt", st)].total == _sum(res, "pt", st)
    assert owed[("pt", "KA")].due_date == date(2025, 5, 20)
    # no line for something nobody owes
    assert all(x.total > 0 for x in owed.values())
    # the files say the same, and the reconciliation checked it
    checks = [c for c in april.files["reconciliation"] if c["check"].startswith("Government due")]
    assert len(checks) == len(owed) and all(c["matches"] for c in checks)
    # finalising again (a retried step) adds nothing
    assert len(dues.create_for_run(april)) == len(owed) and StatutoryDue.objects.filter(run=april).count() == len(owed)
    assert "government payments to make" in april.events.get(action="finalized").message


def test_due_dates_follow_the_rule_and_the_calendar(statutory_versions):
    from payroll_engine.periods import Period

    assert dues._due_day(statutory_versions, "income_tax", Period(2026, 3), "IN") == date(2026, 4, 30)  # March TDS: 30 April
    assert dues._due_day(statutory_versions, "income_tax", Period(2026, 2), "IN") == date(2026, 3, 7)
    assert dues._due_day(statutory_versions, "professional_tax", Period(2026, 1), "IN-MH") == date(2026, 2, 28)  # no 30 Feb
    assert dues._due_day(statutory_versions, "professional_tax", Period(2025, 12), "IN-MH") == date(2026, 1, 30)


def test_status_is_worked_out_from_the_dates():
    due = StatutoryDue(due_date=date(2025, 8, 15))
    assert dues.status_of(due, date(2025, 8, 1))["code"] == "due"
    assert dues.status_of(due, date(2025, 8, 13))["label"] == "Due in 2 days"
    assert dues.status_of(due, date(2025, 8, 15))["label"] == "Due today"
    assert dues.status_of(due, date(2025, 8, 16))["label"] == "Overdue by 1 day"
    due.paid_on = date(2025, 8, 15)
    assert dues.status_of(due, date(2025, 9, 1))["code"] == "paid"
    due.paid_on = date(2025, 8, 18)
    assert dues.status_of(due, date(2025, 9, 1))["label"] == "Paid 3 days late"


def test_recording_and_undoing_a_payment(demo):
    company, _ = demo
    april = setup.run_month(company, "2025-04", log=lambda *a: None)
    maker, checker = login("maker"), login("checker")
    # Government dues are the finance head's (checker) only.
    assert maker.get(f"/api/companies/{company.pk}/dues").status_code == 403
    assert maker.get(f"/api/companies/{company.pk}/overview").json()["dues"] is None
    listing = checker.get(f"/api/companies/{company.pk}/dues?status=open").json()
    assert listing["summary"]["open"] == april.dues.count() and listing["summary"]["paid"] == 0
    assert all(x["status"]["code"] == "overdue" for x in listing["dues"])  # 2025 dues, seen from today
    pf = april.dues.get(kind="pf")
    url = f"/api/dues/{pf.pk}"
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
    assert "future" in checker.post(f"{url}/paid", {"paid_on": tomorrow, "reference": "X"}, content_type=JSON).json()["detail"]
    assert "before April 2025 began" in checker.post(f"{url}/paid", {"paid_on": "2025-03-31", "reference": "X"}, content_type=JSON).json()["detail"]
    assert "challan" in checker.post(f"{url}/paid", {"paid_on": "2025-05-14", "reference": " "}, content_type=JSON).json()["detail"]
    ok = checker.post(f"{url}/paid", {"paid_on": "2025-05-14", "reference": "TRRN 1234567890"}, content_type=JSON).json()
    assert ok["status"]["code"] == "paid" and ok["reference"] == "TRRN 1234567890" and ok["paid_by"]["username"] == "checker"
    assert maker.post(f"{url}/undo", {"reason": "x"}, content_type=JSON).status_code == 403
    assert "already recorded as paid" in checker.post(f"{url}/paid", {"paid_on": "2025-05-14", "reference": "Y"}, content_type=JSON).json()["detail"]
    event = RunEvent.objects.filter(run=april, action="due_paid").get()
    assert "TRRN 1234567890" in event.message and event.detail["total"] == str(pf.total).rstrip("0").rstrip(".")
    assert "why" in checker.post(f"{url}/undo", {"reason": ""}, content_type=JSON).json()["detail"]
    undone = checker.post(f"{url}/undo", {"reason": "Wrong challan entered"}, content_type=JSON).json()
    assert undone["paid_on"] is None and RunEvent.objects.filter(run=april, action="due_paid_undone").exists()
    late = checker.post(f"{url}/paid", {"paid_on": "2025-05-20", "reference": "TRRN 99"}, content_type=JSON).json()
    assert late["status"] == {"code": "paid_late", "label": "Paid 5 days late", "days": 5}
    assert checker.get(f"/api/runs/{april.pk}").json()["dues"][0]["title"] and maker.get(f"/api/runs/{april.pk}").json()["dues"] == []


def test_the_demo_history_records_past_payments_as_made(demo):
    company, _ = demo
    april = setup.run_month(company, "2025-04", log=lambda *a: None)
    assert setup.record_past_payments(april) == april.dues.count()
    assert all(x.paid_on and x.paid_on <= x.due_date and x.reference.startswith("DEMO-") for x in april.dues.all())


# --------------------------------------------------------------------------- day count per month

def test_day_count_set_when_opening_a_month_decides_one_days_pay(demo):
    company, _ = demo
    maker, checker = login("maker"), login("checker")
    for bad in ("0", "32", "26.5", "abc"):
        r = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-07", "day_count": bad}, content_type=JSON)
        assert r.status_code == 400, bad
    run = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-07", "day_count": "26"}, content_type=JSON).json()
    assert run["day_count"] == {"days": 26, "source": "set for July 2025", "set_for_month": True}
    rid = run["id"]
    maker.post(f"/api/runs/{rid}/review-data", content_type=JSON)
    maker.post(f"/api/runs/{rid}/add-by-hand", {"kind": "extra_unpaid_days", "employee_code": "BL002", "days": "2",
                                                "reason": "Absent without approval"}, content_type=JSON)
    maker.post(f"/api/runs/{rid}/calculate", content_type=JSON)
    emp = next(e for e in maker.get(f"/api/runs/{rid}").json()["result"]["employees"] if e["code"] == "BL002")
    rates = {k.split(".", 1)[1]: Decimal(v) for k, v in emp["amounts"].items() if k.startswith("rate.")}
    unpaid = Decimal(emp["amounts"]["unpaid_days"])
    assert unpaid >= 2
    assert Decimal(emp["amounts"]["lop_deduction"]) == sum(_round(r * unpaid / 26) for r in rates.values())
    def texts(lines):
        for line in lines:
            yield f"{line.get('formula', '')} {line.get('reason', '')}"
            yield from texts(line.get("children", []))
    assert any("26 days (set for July 2025)" in t for t in texts(emp["lines"]))

    # Changing it sends the calculated month back; the same number as the company setting means "the setting".
    out = maker.post(f"/api/runs/{rid}/day-count", {"day_count": "31"}, content_type=JSON).json()["run"]
    assert out["status"] == "draft" and out["day_count"] == {"days": 31, "source": "days in July", "set_for_month": False}
    assert PayrollRun.objects.get(pk=rid).day_count is None
    assert "was ÷ 26" in out["events"][-1]["message"]
    assert checker.post(f"/api/runs/{rid}/day-count", {"day_count": "30"}, content_type=JSON).status_code == 403
    maker.post(f"/api/runs/{rid}/day-count", {"day_count": "26"}, content_type=JSON)
    maker.post(f"/api/runs/{rid}/calculate", content_type=JSON)
    maker.post(f"/api/runs/{rid}/submit", content_type=JSON)
    checker.post(f"/api/runs/{rid}/approve", {"note": "ok"}, content_type=JSON)
    assert maker.post(f"/api/runs/{rid}/day-count", {"day_count": "30"}, content_type=JSON).status_code == 400  # final

    # A day of July found unpaid later is worked out with July's own day count (26), not August's.
    setup.import_spreadsheet(company, REPO / "data/brickline/brickline_hr_data.xlsx", log=lambda *a: None)  # read again after July
    aug = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-08"}, content_type=JSON).json()
    assert aug["day_count"]["days"] == 31 and not aug["day_count"]["set_for_month"]
    maker.post(f"/api/runs/{aug['id']}/add-by-hand", {"kind": "past_unpaid_days", "employee_code": "BL002", "month": "2025-07",
                                                      "days": "1", "reason": "Absence found later"}, content_type=JSON)
    maker.post(f"/api/runs/{aug['id']}/review-data", content_type=JSON)
    maker.post(f"/api/runs/{aug['id']}/calculate", content_type=JSON)
    arrears = Decimal(next(e for e in maker.get(f"/api/runs/{aug['id']}").json()["result"]["employees"] if e["code"] == "BL002")["amounts"]["arrears"])
    expected = sum(_round(r * unpaid / 26) - _round(r * (unpaid + 1) / 26) for r in rates.values())
    assert arrears == expected < 0


# --------------------------------------------------------------------------- opening balances

def _csv_file(text, name="opening.csv"):
    return SimpleUploadedFile(name, text.encode())


def test_opening_balances_feed_tds_for_a_company_that_starts_mid_year(demo):
    company, _ = demo
    maker, checker = login("maker"), login("checker")
    url = f"/api/companies/{company.pk}/opening-balances"
    assert "April" in maker.post(url, {"period": "2025-04", "file": _csv_file("Employee ID,Gross paid this year,TDS this year\n")}).json()["detail"]
    bad = maker.post(url, {"period": "2025-10", "file": _csv_file("Employee ID,Gross paid this year,TDS this year\nBL001,600000,30000\nZZ9,1,0\nBL002,100,500\n")})
    assert bad.status_code == 400 and "ZZ9 is not in the reviewed HR data" in bad.json()["detail"] and "more than the pay" in bad.json()["detail"]
    assert maker.get(url, {"period": "2025-10"}).json()["count"] == 0  # nothing saved
    assert checker.post(url, {"period": "2025-10", "file": _csv_file("Employee ID,Gross paid this year,TDS this year\nBL001,1,0\n")}).status_code == 403

    def october_tds():
        run = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-10"}, content_type=JSON).json()
        maker.post(f"/api/runs/{run['id']}/review-data", content_type=JSON)
        maker.post(f"/api/runs/{run['id']}/calculate", content_type=JSON)
        emp = next(e for e in maker.get(f"/api/runs/{run['id']}").json()["result"]["employees"] if e["code"] == "BL001")
        return run["id"], emp["amounts"]

    first_id, without = october_tds()
    maker.post(f"/api/runs/{first_id}/cancel", content_type=JSON)
    ok = maker.post(url, {"period": "2025-10", "file": _csv_file("Employee ID,Gross paid this year,TDS this year\nBL001,\"9,00,000\",45000\n")}).json()
    assert ok["count"] == 1 and ok["gross"] == "900000" and ok["tds"] == "45000" and ok["covers"] == "April 2025 to September 2025"
    assert any(m["employee_code"] == "BL002" for m in ok["missing"])
    rid, with_ob = october_tds()
    assert with_ob["tds_paid_before"] == "45000" and without["tds_paid_before"] == "0"
    assert Decimal(with_ob["tds_projected_income"]) - Decimal(without["tds_projected_income"]) == 900000
    assert without["tds"] == "0" and Decimal(with_ob["tds"]) > 0  # 9 lakh earlier in the year takes her past the rebate
    maker.post(f"/api/runs/{rid}/submit", content_type=JSON)
    checker.post(f"/api/runs/{rid}/approve", {"note": "ok"}, content_type=JSON)
    again = maker.post(url, {"period": "2025-10", "file": _csv_file("Employee ID,Gross paid this year,TDS this year\nBL001,1,0\n")})
    assert again.status_code == 400 and "can no longer change" in again.json()["detail"]


# --------------------------------------------------------------------------- adding a company

def test_onboarding_a_new_company_through_the_api(demo):
    maker, checker = login("maker"), login("checker")
    assert checker.post("/api/companies", {"name": "Northwind", "state": "MH"}, content_type=JSON).status_code == 403
    assert "PAN" in maker.post("/api/companies", {"name": "Northwind", "state": "MH", "pan": "123"}, content_type=JSON).json()["detail"]
    assert "not an Indian state" in maker.post("/api/companies", {"name": "Northwind", "state": "Atlantis"}, content_type=JSON).json()["detail"]
    co = maker.post("/api/companies", {"name": "Northwind Interiors Pvt Ltd", "state": "Maharashtra", "city": "Mumbai",
                                       "pan": "aabcn1234q", "tan": "MUMN12345A", "pay_day": 1}, content_type=JSON).json()
    assert co["state"] == "MH" and co["pan"] == "AABCN1234Q" and co["code"] == "northwind-interiors-pvt-ltd"
    assert maker.post("/api/companies", {"name": "northwind interiors pvt ltd", "state": "MH"}, content_type=JSON).status_code == 400
    cid = co["id"]
    steps = maker.get(f"/api/companies/{cid}/onboarding").json()["steps"]
    assert steps["company"] and not steps["details_complete"] and not steps["settings"] and not steps["hr_data"]["done"]
    assert maker.patch(f"/api/companies/{cid}", {"pf_establishment_code": "MHBAN0012345000", "esi_employer_code": "31000123450001000"},
                       content_type=JSON).json()["pf_establishment_code"] == "MHBAN0012345000"
    r = maker.post(f"/api/companies/{cid}/policies", {"key": "lop", "effective_from": "2025-04-01", "payload": {"divisor": "30"}},
                   content_type=JSON)
    assert r.status_code == 201
    job = maker.post(f"/api/companies/{cid}/uploads", {"employees": _sheet_file("Employees"), "compensation": _sheet_file("Salary"),
                                                       "leave": _sheet_file("Leave")}).json()
    job = maker.post(f"/api/jobs/{job['id']}/suggest-mapping").json()
    for t in job["tables"]:
        if not t["mapping"]["confirmed"]:
            maker.post(f"/api/tables/{t['id']}/confirm-mapping", {"mapping": t["mapping"]["mapping"]}, content_type=JSON)
    snap = maker.post(f"/api/jobs/{job['id']}/build-snapshot").json()
    maker.post(f"/api/snapshots/{snap['id']}/approve", {"warnings_read": True}, content_type=JSON)
    steps = maker.get(f"/api/companies/{cid}/onboarding").json()["steps"]
    assert steps["details_complete"] and steps["settings"] and steps["hr_data"]["done"] and steps["data_checked"]["done"]
    assert steps["data_checked"]["employees"] > 0 and not steps["first_month"]["done"]
    run = maker.post(f"/api/companies/{cid}/runs", {"period": "2025-10"}, content_type=JSON).json()
    assert run["day_count"] == {"days": 30, "source": "company setting", "set_for_month": False}
    steps = maker.get(f"/api/companies/{cid}/onboarding").json()["steps"]
    assert steps["first_month"] == {"done": True, "run_id": run["id"], "period": "2025-10"}
    names = [c["name"] for c in maker.get("/api/companies").json()]
    assert names[-1] == "Northwind Interiors Pvt Ltd" and len(names) == 2
    # The new company's data and months are its own.
    assert all(r["period"] == "2025-10" for r in maker.get(f"/api/companies/{cid}/runs").json())


@pytest.mark.django_db
def test_onboarding_says_which_professional_tax_applies_to_the_company_state():
    from payroll.demo import setup
    from payroll.features.onboarding.onboarding import professional_tax_for

    setup.load_statutory_rules()
    tg = professional_tax_for("Telangana")
    assert tg["status"] == "slabs" and tg["due_day"] == 10 and "apply automatically" in tg["message"] and tg["verify"]
    gj = professional_tax_for("GJ")
    assert gj["status"] == "slabs" and "warning" in gj  # no due day yet: finalising would stop, so say so now
    assert professional_tax_for("Delhi")["status"] == "not_charged"
    assert professional_tax_for("Tamil Nadu")["status"] == "not_supported"
    assert professional_tax_for("KA")["status"] == "slabs" and "verify" not in professional_tax_for("KA")


def test_unpaid_absence_days_must_match_its_dates(demo):
    """From-To decides the count: all the days in between, or half a day less. 1 day over 11-20 Jul is refused."""
    company, _ = demo
    maker = login("maker")
    rid = maker.post(f"/api/companies/{company.pk}/runs", {"period": "2025-07"}, content_type=JSON).json()["id"]
    maker.post(f"/api/runs/{rid}/review-data", content_type=JSON)

    def add(days, start, end):
        return maker.post(f"/api/runs/{rid}/add-by-hand", {"kind": "extra_unpaid_days", "employee_code": "BL002", "days": days,
                                                           "from": start, "to": end, "reason": "Absent without approval"},
                          content_type=JSON)

    r = add("1", "2025-07-11", "2025-07-20")
    assert r.status_code == 400 and "is 10 days" in r.json()["detail"]
    assert add("11", "2025-07-11", "2025-07-20").status_code == 400   # more days than the dates hold
    assert add("3", "2025-07-01", "2025-07-03").status_code == 201
    assert add("1.5", "2025-07-07", "2025-07-08").status_code == 201  # a half day at one end
    assert add("0.5", "2025-07-09", "2025-07-09").status_code == 201
