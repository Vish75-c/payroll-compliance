import pytest

from payroll_engine.engine import calculate_employee
from payroll_engine.periods import Period

from .conftest import company_setting


def pay(month, basic, versions, **fields):
    employee = {
        "code": "ARREARS",
        "work_state": "KA",
        "compensation": [{"effective_from": "2025-04-01", "components": {"basic": basic}}],
        **fields,
    }
    return calculate_employee(employee, Period.parse(month), versions, {"state": "KA"})


def april_basis(result, versions, pf_applicable=True):
    facts = result["facts"]
    return {
        "period": "2025-04",
        "policy_versions": versions,
        "pf_applicable": pf_applicable,
        "unpaid_days": facts["unpaid_days"],
        "paid": {
            "earned": facts["earned"],
            "regular_gross": facts["regular_gross"],
            "pf_employee": facts["pf_employee_regular"],
            "pf_employer": facts["pf_employer_regular"],
            "eps_employer": facts["eps_employer_regular"],
        },
    }


@pytest.mark.parametrize("old_basic,new_basic,paid_gross,new_gross,arrears", [
    ("30014", "30015", "29014", "29014", "0"),
    ("30000.4", "30000.5", "29000", "29001", "1"),
    ("30000.5", "30000.4", "29001", "29000", "-1"),
])
def test_gross_only_arrears_use_cash_difference(statutory_versions, old_basic, new_basic, paid_gross, new_gross, arrears):
    versions = statutory_versions + [company_setting("rounding", {"method": "nearest", "earnings": "gross_only"})]
    paid = pay("2025-04", old_basic, versions, unpaid_days="1", pf_applicable=False)
    corrected = pay("2025-04", new_basic, versions, unpaid_days="1", pf_applicable=False)
    basis = april_basis(paid, versions, pf_applicable=False)
    result = pay("2025-05", new_basic, versions, arrears_basis=[basis], pf_applicable=False)

    assert paid["amounts"]["regular_gross"] == paid_gross
    assert corrected["amounts"]["regular_gross"] == new_gross
    assert result["amounts"]["arrears"] == arrears
    assert result["facts"]["arrears_detail"][0]["total"] == arrears


def test_unchanged_legacy_gross_only_pay_does_not_create_arrears(statutory_versions):
    versions = statutory_versions + [company_setting("rounding", {"method": "nearest", "earnings": "gross_only"})]
    paid = pay("2025-04", "30015", versions, unpaid_days="1", pf_applicable=False)
    basis = april_basis(paid, versions, pf_applicable=False)
    del basis["paid"]["regular_gross"], basis["paid"]["eps_employer"]
    result = pay("2025-05", "30015", versions, arrears_basis=[basis], pf_applicable=False)

    assert result["amounts"]["arrears"] == "0"
    assert result["facts"]["arrears_detail"] == []


@pytest.mark.parametrize("old_basic,new_basic,arrears_pf,arrears_eps,pf_total,eps_total,epf_total", [
    ("10000", "12000", "240", "167", "1680", "1167", "513"),
    ("12000", "10000", "-240", "-167", "960", "666", "294"),
])
def test_pf_arrears_preserve_signed_eps_and_epf_split(
    statutory_versions, old_basic, new_basic, arrears_pf, arrears_eps, pf_total, eps_total, epf_total,
):
    paid = pay("2025-04", old_basic, statutory_versions)
    basis = april_basis(paid, statutory_versions)
    result = pay("2025-05", new_basic, statutory_versions, arrears_basis=[basis])

    assert result["amounts"]["pf_employer"] == pf_total
    assert result["amounts"]["eps_employer"] == eps_total
    assert result["amounts"]["epf_employer"] == epf_total
    assert result["facts"]["arrears_detail"][0]["pf_employer"] == arrears_pf
    assert result["facts"]["arrears_detail"][0]["eps_employer"] == arrears_eps


def test_past_pf_is_applied_using_past_eligibility_and_is_not_repeated(statutory_versions):
    paid = pay("2025-04", "10000", statutory_versions)
    result = pay("2025-05", "12000", statutory_versions,
                 arrears_basis=[april_basis(paid, statutory_versions)], pf_applicable=False)

    assert result["amounts"]["pf_employee"] == "240"
    assert result["amounts"]["pf_employer"] == "240"
    assert result["amounts"]["eps_employer"] == "167"
    assert result["amounts"]["epf_employer"] == "73"
    assert result["facts"]["eps_employer_regular"] == "0"

    settled = april_basis(pay("2025-04", "12000", statutory_versions), statutory_versions)
    next_result = pay("2025-06", "12000", statutory_versions, arrears_basis=[settled], pf_applicable=False)
    assert next_result["facts"]["arrears_detail"] == []


def test_pension_only_correction_still_applies_when_total_pf_is_already_settled(statutory_versions):
    paid = pay("2025-04", "12000", statutory_versions)
    basis = april_basis(paid, statutory_versions)
    basis["paid"]["eps_employer"] = "833"  # An older calculation allocated the full arrears to EPF.
    result = pay("2025-05", "12000", statutory_versions, arrears_basis=[basis])

    assert result["amounts"]["arrears"] == "0"
    assert result["amounts"]["pf_employer"] == "1440"
    assert result["amounts"]["eps_employer"] == "1167"
    assert result["amounts"]["epf_employer"] == "273"
    assert result["facts"]["arrears_detail"][0]["eps_employer"] == "167"
