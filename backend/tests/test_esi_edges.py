import pytest

from payroll_engine.engine import calculate_employee
from payroll_engine.periods import Period


def _payroll(period, salary, versions, **employee_facts):
    employee = {
        "code": "E1",
        "compensation": [{"effective_from": "2025-04-01", "components": {"basic": salary}}],
        **employee_facts,
    }
    return calculate_employee(employee, Period.parse(period), versions, {"state": "KA"})


@pytest.mark.parametrize(("period", "covered_month", "applies", "covered"), [
    ("2025-07", "2025-04", True, True),
    ("2025-07", "2025-07", True, False),  # history must be earlier than the pay month
    ("2025-10", "2025-09", True, False),  # the period ends in September
    ("2026-01", "2025-10", True, True),   # coverage continues across the calendar year
    ("2026-04", "2026-03", True, False),  # the period ends in March
    ("2025-07", "2025-04", False, False),  # explicit employee exclusion takes precedence
])
def test_esi_coverage_after_wages_exceed_the_ceiling(statutory_versions, period, covered_month, applies, covered):
    result = _payroll(period, "23000", statutory_versions, esi_applicable=applies, covered_esi_months=[covered_month])
    amounts = result["amounts"]

    assert result["facts"]["esi_covered"] is covered
    assert (amounts["esi_employee"], amounts["esi_employer"]) == (("173", "748") if covered else ("0", "0"))


@pytest.mark.parametrize(("prior_coverage", "covered"), [([], False), (["2025-04"], True)])
def test_zero_pay_preserves_existing_esi_coverage_without_establishing_new_coverage(statutory_versions, prior_coverage, covered):
    result = _payroll("2025-07", "18000", statutory_versions, unpaid_days="31", covered_esi_months=prior_coverage)

    assert result["facts"]["esi_covered"] is covered
    assert result["amounts"]["esi_employee"] == result["amounts"]["esi_employer"] == "0"


@pytest.mark.parametrize(("salary", "employee_share"), [("4576", "0"), ("4577", "35")])
def test_monthly_rated_esi_daily_wage_exemption_is_inclusive(statutory_versions, salary, employee_share):
    result = _payroll("2025-07", salary, statutory_versions)

    # The statutory average divides by 26 even in a 31-day pay month. ₹4,576 / 26 is exactly ₹176.
    assert result["facts"]["esi_covered"] is True
    assert result["amounts"]["esi_employee"] == employee_share
    assert result["amounts"]["esi_employer"] == "149"


def test_supplied_esi_average_daily_wage_overrides_monthly_rated_default(statutory_versions):
    result = _payroll("2025-07", "18000", statutory_versions, esi_average_daily_wage="175")

    assert result["facts"]["esi_covered"] is True
    assert result["amounts"]["esi_employee"] == "0"
    assert result["amounts"]["esi_employer"] == "585"
