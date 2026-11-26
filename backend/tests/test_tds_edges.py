from decimal import Decimal

import pytest

from payroll_engine import policies
from payroll_engine.engine import calculate_employee
from payroll_engine.periods import Period

from .conftest import company_setting


@pytest.mark.parametrize(("one_time_pay", "already_withheld", "annual_tax", "expected_tds"), [
    ("50000", "100000", "140400", "25400"),  # ₹15,000 regular withholding plus ₹10,400 bonus tax
    ("50000", "132000", "140400", "8400"),   # ₹2,000 above ordinary annual tax credits the bonus tax
    ("50000", "200000", "140400", "0"),      # prior withholding covers the whole projected annual liability
    ("-50000", "115000", "120900", "0"),     # a recovery can lower withholding to zero
])
def test_tax_in_month_credits_prior_withholding(statutory_versions, one_time_pay, already_withheld, annual_tax, expected_tds):
    employee = {
        "code": "E1",
        "compensation": [{"effective_from": "2025-04-01", "components": {"basic": "200000"}}],
        "ytd": {"opening_gross": "1300000", "opening_tds": already_withheld},
        "one_time": [{"kind": "earning", "code": "bonus", "amount": one_time_pay}],
    }
    versions = statutory_versions + [company_setting("tds_policy", {"one_time_income": "tax_in_month"})]
    result = calculate_employee(employee, Period.parse("2026-02"), versions, {"state": "KA"})

    assert result["amounts"]["tds_annual_tax"] == annual_tax
    assert result["amounts"]["tds"] == expected_tds


@pytest.mark.parametrize("scope", [policies.MONTH, policies.OVERRIDE])
def test_projection_uses_company_divisor_without_carrying_this_months_override(statutory_versions, scope):
    employee = {
        "code": "E1",
        "date_of_exit": "2025-07-20",
        "compensation": [{"effective_from": "2025-04-01", "components": {"basic": "600000"}}],
        "ytd": {"opening_gross": "1200000"},
    }
    versions = statutory_versions + [company_setting("lop", {"divisor": "26"})]
    june_rule = {**company_setting("lop", {"divisor": "30"}, "2025-06-01"), "scope": scope}
    result = calculate_employee(employee, Period.parse("2025-06"), versions + [june_rule], {"state": "KA"})

    # July pays ₹6,00,000 less 11 days at the company divisor of 26.
    assert result["amounts"]["tds_projected_income"] == "2146154"
    assert result["amounts"]["tds"] == "113250"


def test_projection_tracks_salary_revisions_during_the_exit_month(statutory_versions):
    employee = {
        "code": "E1",
        "date_of_exit": "2025-07-20",
        "compensation": [
            {"effective_from": "2025-04-01", "components": {"basic": "30000"}},
            {"effective_from": "2025-07-16", "components": {"basic": "60000"}},
        ],
        "unpaid_days": "1",
    }
    amounts = calculate_employee(employee, Period.parse("2025-06"), statutory_versions, {"state": "KA"})["amounts"]
    projected_july = Decimal(amounts["tds_projected_income"]) - Decimal(amounts["gross"])

    # July projects 15 days at ₹30,000/month and 5 at ₹60,000/month with no assumed unpaid leave.
    assert projected_july == Decimal("24194")


def test_projection_uses_and_records_scheduled_company_rules(statutory_versions):
    employee = {
        "code": "E1",
        "date_of_exit": "2025-08-20",
        "compensation": [{"effective_from": "2025-04-01", "components": {"basic": "600000"}}],
    }
    lop = company_setting("lop", {"divisor": "26"}, "2025-08-01")
    rounding = company_setting("rounding", {"method": "nearest", "earnings": "gross_only"}, "2025-07-01")
    result = calculate_employee(employee, Period.parse("2025-06"), statutory_versions + [lop, rounding], {"state": "KA"})
    tds = next(line for line in result["lines"] if line["code"] == "tds")
    projection = next(child for child in tds["children"] if child["code"] == "tds.projection")

    assert projection["amount"] == "1546154"
    assert {(r["key"], r["version_id"]) for r in projection["rules"]} == {("lop", lop["id"]), ("rounding", rounding["id"])}
    assert len(projection["rules"]) == 2


@pytest.mark.parametrize("method", ["nearest", "up", "down"])
@pytest.mark.parametrize("one_time_income", ["spread", "tax_in_month"])
def test_final_month_withholds_the_exact_remaining_amount(statutory_versions, method, one_time_income):
    employee = {
        "code": "E1",
        "compensation": [{"effective_from": "2025-04-01", "components": {"basic": "200000"}}],
        "ytd": {"opening_gross": "1500000", "opening_tds": "140399.75"},
        "one_time": [{"kind": "earning", "code": "bonus", "amount": "50000"}],
    }
    setting = company_setting("tds_policy", {"monthly_rounding": method, "one_time_income": one_time_income})
    amounts = calculate_employee(employee, Period(2026, 3), statutory_versions + [setting], {"state": "KA"})["amounts"]

    assert amounts["tds_annual_tax"] == "140400"
    assert amounts["tds"] == "0.25"
