"""The engine receives the amounts actually paid in prior months."""
from datetime import date

import pytest

from payroll.demo import setup
from payroll.features.hr_data.models import DataSnapshot
from payroll.features.payroll_runs.inputs import build_run_input
from payroll.features.payroll_runs.models import PayrollRun
from payroll.features.rules import policies
from payroll_engine.engine import calculate_month


pytestmark = pytest.mark.django_db


def _month(company, period, basic, **employee_fields):
    snapshot = DataSnapshot.objects.create(
        company=company, status="approved", source_kind="spreadsheet", source_system="Synthetic",
        data={
            "employees": [{"code": "E1", "name": "Test Employee", "work_state": "KA", "gender": "F",
                           **employee_fields}],
            "compensation": [{"employee_code": "E1", "effective_from": "2025-04-01",
                              "components": {"basic": str(basic)}}],
        },
    )
    return PayrollRun.objects.create(company=company, snapshot=snapshot, period=period,
                                     knowledge_date=date(2025, int(period[-2:]), 25))


def _record_paid(run, legacy=False):
    run.input = build_run_input(run)
    run.result = calculate_month(run.input)
    if legacy:
        employee = run.result["employees"][0]
        facts = employee["facts"]
        employee["amounts"]["eps_employer"] = facts.pop("eps_employer_regular")
        employee["amounts"]["epf_employer"] = str(int(employee["amounts"]["pf_employer"]) - int(employee["amounts"]["eps_employer"]))
        facts.pop("regular_gross")
        facts.pop("esi_covered", None)
        for detail in facts["arrears_detail"]:
            detail.pop("eps_employer")
    run.status = PayrollRun.FINALIZED
    run.save(update_fields=["input", "result", "status"])
    return run.result["employees"][0]["amounts"]


@pytest.mark.parametrize("legacy_may", [False, True])
def test_archived_payroll_preserves_the_pension_already_paid(legacy_may):
    setup.load_statutory_rules()
    company = setup.ensure_company("historic-engine", "Historical Engine Test", "KA", "")
    _record_paid(_month(company, "2025-04", 10000), legacy=True)
    may = _record_paid(_month(company, "2025-05", 12000), legacy=legacy_may)
    assert may["arrears"] == "2000"
    assert may["eps_employer"] == ("1000" if legacy_may else "1167")

    june = _month(company, "2025-06", 12000)
    june_input = build_run_input(june)
    assert june_input["employees"][0]["arrears_basis"][0]["paid"]["regular_gross"] == "12000"
    result = calculate_month(june_input)["employees"][0]
    assert result["amounts"]["arrears"] == "0"
    assert result["amounts"]["pf_employer"] == "1440"
    assert result["amounts"]["eps_employer"] == ("1167" if legacy_may else "1000")
    assert result["amounts"]["epf_employer"] == ("273" if legacy_may else "440")


def test_known_future_earnings_setting_flows_into_tds_forecast():
    setup.load_statutory_rules()
    company = setup.ensure_company("planned-lop", "Planned Leave Test", "KA", "")
    maker = setup.system_user("maker")
    policies.add_version(company=company, user=maker, key="lop", jurisdiction="", effective_from=date(2025, 9, 1),
                         payload={"divisor": 26}, title="September day rate", note="")
    run = _month(company, "2025-04", 100000, date_of_exit="2025-09-15")

    result = calculate_month(build_run_input(run))["employees"][0]["amounts"]

    assert result["regular_gross"] == "100000"
    assert result["tds_projected_income"] == "542308"


def test_esi_continues_using_coverage_from_an_archived_payroll():
    setup.load_statutory_rules()
    company = setup.ensure_company("esi-history", "ESI History Test", "KA", "")
    _record_paid(_month(company, "2025-06", 20000), legacy=True)
    july = _month(company, "2025-07", 23000)
    data = july.snapshot.data
    data["compensation"][0]["effective_from"] = "2025-07-01"
    data["compensation"].append({"employee_code": "E1", "effective_from": "2025-04-01", "components": {"basic": "20000"}})
    july.snapshot.save(update_fields=["data"])

    inputs = build_run_input(july)
    amounts = calculate_month(inputs)["employees"][0]["amounts"]

    assert inputs["employees"][0]["covered_esi_months"] == ["2025-06"]
    assert amounts["gross"] == "23000"
    assert amounts["esi_employee"] == "173"
    assert amounts["esi_employer"] == "748"
