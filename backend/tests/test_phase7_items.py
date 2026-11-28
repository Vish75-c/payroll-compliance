"""Perquisites, a TDS set by hand, payments on leaving (with their tax exemptions and the final-pay deadline), the
company's NPS and the Labour Welfare Fund. The LWF rule here is made up: real amounts are entered per state as data."""
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from payroll.features.payroll_runs.exports import build_files, reconcile
from payroll_engine.engine import calculate_month

from .test_engine import amounts, emp, run

LWF_KA = {"id": "lwf-ka", "key": "lwf", "scope": "statutory", "jurisdiction": "IN-KA", "effective_from": "2025-01-01",
          "payload": {"months": [12], "employee": "20", "employer": "40"}, "title": "Made-up KA LWF"}


def one_time(kind, amount, code="x", label="X", **kw):
    return {"ref": "t", "kind": kind, "code": code, "label": label, "amount": str(amount), **kw}


def test_a_perquisite_raises_tds_but_not_gross_or_net_before_tax(statutory_versions):
    base = amounts(run(emp("K", 150000), "2025-06", statutory_versions))
    r = run(emp("K", 150000, one_time=[one_time("perquisite", 40000, "esop", "ESOP")]), "2025-06", statutory_versions)
    a = amounts(r)
    assert a["gross"] == base["gross"] and a["tds"] > base["tds"]
    assert a["net_pay"] == base["net_pay"] - (a["tds"] - base["tds"])  # only the extra tax comes out of pay
    line = next(l for l in r["lines"] if l["code"] == "perquisite.esop")
    assert line["label"] == "ESOP (perquisite)" and "not paid in cash" in line["reason"]


def test_a_tds_set_for_the_month_is_used_and_the_worked_out_one_is_shown(statutory_versions):
    worked = amounts(run(emp("K", 150000), "2025-04", statutory_versions))["tds"]
    r = run(emp("K", 150000, one_time=[one_time("tds_override", 5000)]), "2025-04", statutory_versions)
    a = amounts(r)
    assert a["tds"] == 5000 and a["tds_worked_out"] == worked
    assert next(l for l in r["lines"] if l["code"] == "tds")["formula"] == f"TDS set for this month: ₹5,000 (worked out: ₹{worked:,.0f})"


def _leaver(doj, doe, **kw):
    return emp("G", 100000, date_of_joining=doj, date_of_exit=doe, **kw)  # basic ₹50,000


def test_gratuity_is_exempt_up_to_the_least_of_the_limits(statutory_versions):
    # 1 Jan 2015 to 30 Jun 2025: 10 years 5 months, so 10 years. 15/26 × ₹50,000 × 10 = ₹2,88,462 is exempt.
    r = run(_leaver("2015-01-01", "2025-06-30", one_time=[one_time("earning", 500000, "gratuity", "Gratuity", taxable=True,
                                                                     exemption="gratuity")]), "2025-06", statutory_versions)
    assert amounts(r)["exempt.gratuity"] == 288462
    assert "15/26 × ₹50,000 basic + DA × 10 years = ₹2,88,462" in next(l for l in r["lines"] if l["code"] == "one_time.gratuity")["reason"]
    short = run(_leaver("2022-01-01", "2025-06-30", one_time=[one_time("earning", 100000, "gratuity", "Gratuity", taxable=True,
                                                                         exemption="gratuity")]), "2025-06", statutory_versions)
    assert "exempt.gratuity" not in short["amounts"]  # under 5 years: all of it is taxed
    died = run(_leaver("2022-01-01", "2025-06-30", exit_reason="D", one_time=[one_time("earning", 100000, "gratuity", "Gratuity",
                                                                                        taxable=True, exemption="gratuity")]), "2025-06", statutory_versions)
    assert amounts(died)["exempt.gratuity"] > 0  # on death there is no minimum service


def test_leave_encashment_on_leaving_is_exempt_up_to_25_lakh(statutory_versions):
    r = run(_leaver("2015-01-01", "2025-06-30", one_time=[one_time("earning", 3000000, "leave_encashment", "Leave encashment", taxable=True,
                                                                     exemption="leave_encashment")]), "2025-06", statutory_versions)
    assert amounts(r)["exempt.leave_encashment"] == 2500000


def test_company_nps_is_a_company_cost_not_a_deduction(statutory_versions):
    base = amounts(run(emp("N", 100000), "2025-06", statutory_versions))
    r = run(emp("N", 100000, nps_percent="10"), "2025-06", statutory_versions)
    a = amounts(r)
    assert a["nps_employer"] == 5000 and a["net_pay"] == base["net_pay"] and a["employer_cost"] == base["employer_cost"] + 5000
    assert any("above 14%" in i["message"] for i in run(emp("N", 100000, nps_percent="15"), "2025-06", statutory_versions)["issues"])


def test_labour_welfare_fund_follows_the_states_rule(statutory_versions):
    versions = statutory_versions + [LWF_KA]
    dec = amounts(run(emp("L", 30000), "2025-12", versions))
    assert (dec["lwf_employee"], dec["lwf_employer"]) == (20, 40)
    assert "lwf_employee" not in run(emp("L", 30000), "2025-06", versions)["amounts"]           # not a month of the rule
    assert "lwf_employee" not in run(emp("L", 30000, lwf_covered=False), "2025-12", versions)["amounts"]
    assert "lwf_employee" not in run(emp("L", 30000), "2025-12", statutory_versions)["amounts"]  # no rule for the state: none


def test_final_pay_deadline_under_the_labour_codes(statutory_versions):
    left = run(emp("D", 30000, date_of_joining="2020-01-01", date_of_exit="2025-12-10"), "2025-12", statutory_versions)  # a Wednesday
    assert any("by 12 Dec 2025" in i["message"] for i in left["issues"])
    before = run(emp("D", 30000, date_of_joining="2020-01-01", date_of_exit="2025-06-11"), "2025-06", statutory_versions)
    assert not any(i.get("code") == "final_pay_deadline" for i in before["issues"])  # before the Codes were in force


def test_the_lwf_and_nps_files_match_the_register(statutory_versions):
    month = {"period": "2025-12", "policy_versions": statutory_versions + [LWF_KA], "company": {"state": "KA"},
             "employees": [emp("A", 30000, nps_percent="10"), emp("B", 50000)]}
    result = calculate_month(month)
    files = build_files(result, month, {"employees": [{"code": "A", "pran": "110012345678"}]})
    assert "nps.csv" in files and "lwf.csv" in files and "110012345678" in files["nps.csv"]
    checks = reconcile(files, result)
    assert all(c["matches"] for c in checks) and {"nps.csv: company nps", "lwf.csv: employee share"} <= {c["check"] for c in checks}


# --------------------------------------------------------------------------- through the month's inputs

@pytest.mark.django_db
def test_perquisites_fixed_tds_and_leaving_payments_from_the_months_sheet(demo):
    from payroll.demo import setup
    from payroll.features.hr_data import snapshots
    from payroll.features.payroll_runs import runs
    from payroll.models import DataSnapshot

    from .test_api import login
    from .test_reading_any_sheet import _xlsx

    company, snapshot = demo
    for m in ("2025-04", "2025-05", "2025-06"):
        setup.run_month(company, m, log=lambda *a: None)
    maker = setup.system_user("maker")
    data = dict(snapshot.data)
    data["employees"] = [dict(e, date_of_exit="2025-07-20", exit_reason="C") if e["code"] == "BL004" else e for e in data["employees"]]
    s = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet", data=data,
                                    content_hash="leaver", checks=[])
    snapshots.approve(s, maker)
    july = runs.open_month(company, "2025-07", maker, snapshot=s)
    runs.review_data(july.pk, maker)
    web = login("maker")
    head = ["Employee Number", "ESOP PERQUISITES", "DIRECT TDS", "GRATUITY", "NOTICE PERIOD PAY", "NOTICE PERIOD RECOVERY"]

    def upload(rows, name):
        sheet = SimpleUploadedFile(name, _xlsx([head, *rows]))
        out = web.post(f"/api/runs/{july.pk}/monthly-inputs", {"file": sheet}).json()["run"]
        return next(c for c in out["changes"] if c["proposal"].get("file") == name)

    wrong = upload([["BL003", "", "", "100000", "", ""]], "wrong.xlsx")  # BL003 does not leave in July
    assert any("does not leave in July 2025, so a payment on leaving cannot be made" in p for p in wrong["proposal"]["problems"])
    web.post(f"/api/changes/{wrong['id']}/discard", content_type="application/json")

    right = upload([["BL001", "40000", "15000", "", "", ""], ["BL004", "", "", "300000", "50000", "8000"]], "right.xlsx")
    assert right["proposal"]["problems"] == []
    assert web.post(f"/api/changes/{right['id']}/confirm", content_type="application/json").status_code == 200
    july = runs.calculate(july.pk, maker)
    people = {e["code"]: e for e in july.result["employees"]}
    priya, kavya = people["BL001"]["amounts"], people["BL004"]["amounts"]
    assert Decimal(priya["tds"]) == 15000 and Decimal(priya["perquisite.esop_perquisites"]) == 40000
    assert Decimal(kavya["one_time.gratuity"]) == 300000 and Decimal(kavya["one_time.notice_period_pay"]) == 50000
    assert Decimal(kavya["deduction.notice_period_recovery"]) == 8000
    assert not any(i.get("code") == "final_pay_deadline" for i in people["BL004"]["issues"])  # July 2025: before the Codes
