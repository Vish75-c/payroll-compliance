from decimal import Decimal
from fractions import Fraction
from itertools import permutations, product

import pytest

from payroll_engine.earnings import compute_earnings, contracted_rate
from payroll_engine.money import round_amount
from payroll_engine.periods import Period
from payroll_engine.steps import EmployeeCalc, regular_pay

from .conftest import company_setting


JULY = Period(2025, 7)


def salary(start, basic, **extra):
    return {"effective_from": start, "components": {"basic": str(basic)}, **extra}


def test_first_salary_uses_the_latest_same_date_correction_even_before_its_start():
    earlier = salary("2025-07-14", 31000, recorded_on="2025-07-15")
    correction = salary("2025-07-14", 62000, recorded_on="2025-07-19")
    for rows in permutations([earlier, correction]):
        rates, _, backfilled = contracted_rate(list(rows), JULY)
        assert rates == {"basic": 62000}
        assert backfilled

    # The normal input path supplies known revisions in order without the recording date.
    result = compute_earnings({"compensation": [salary("2025-07-14", 31000), salary("2025-07-14", 62000)]}, JULY, [], 0)
    assert result.regular_gross == 62000


@pytest.mark.parametrize("divisor,gross", [(26, 13115), (30, 15500)])
def test_fixed_divisor_excludes_salary_versions_that_never_touch_employment(divisor, gross):
    versions = [company_setting("lop", {"divisor": divisor})]
    applies = salary("2025-07-06", 31000)
    employee = {"date_of_joining": "2025-07-07", "date_of_exit": "2025-07-22", "compensation": [applies]}
    baseline = compute_earnings(employee, JULY, versions, 0)
    with_other_rates = compute_earnings({**employee, "compensation": [salary("2025-04-01", 78000), applies,
                                          salary("2025-07-25", 96000)]}, JULY, versions, 0)
    assert with_other_rates == baseline
    assert with_other_rates.regular_gross == gross


def test_weighted_amounts_are_added_before_dividing_and_rounding():
    february = Period(2026, 2)
    employee = {"compensation": [salary("2026-02-01", 36338), salary("2026-02-09", 43300), salary("2026-02-19", 59623)]}
    result = compute_earnings(employee, february, [], 0)
    assert result.rate["basic"] == Decimal("47140.50")
    assert result.regular_gross == 47141


def test_gross_only_rounds_the_full_weighted_amount_at_the_half_rupee():
    february = Period(2026, 2)
    employee = {"compensation": [
        {"effective_from": "2026-02-01", "components": {"basic": "12299", "hra": "14010", "other_allowance": "10648"}},
        {"effective_from": "2026-02-10", "components": {"basic": "15739", "hra": "12667", "other_allowance": "11085"}},
    ]}
    rule = company_setting("rounding", {"method": "nearest", "earnings": "gross_only"})
    result = compute_earnings(employee, february, [rule], 0)
    assert result.regular_gross == 38677


@pytest.mark.parametrize("method", ["nearest", "up", "down", "nearest_even"])
@pytest.mark.parametrize("mode", ["per_component", "gross_only"])
def test_pay_declines_to_zero_as_unpaid_days_reach_employed_days(method, mode):
    employee = {"date_of_exit": "2025-07-20", "compensation": [salary("2025-04-01", 30002), salary("2025-07-16", 60011)]}
    versions = [company_setting("lop", {"divisor": "days_in_month", "day_rate_rounding": {"unit": "0.01", "method": method}}),
                company_setting("rounding", {"method": method, "earnings": mode})]
    gross = []
    for unpaid in (0, 5, 10, 15, Decimal("19.5"), 20):
        result = compute_earnings(employee, JULY, versions, unpaid)
        assert result.contracted_gross == result.regular_gross + result.total_cut
        gross.append(result.regular_gross)
    assert gross == sorted(gross, reverse=True)
    assert gross[-1] == 0
    assert result.earned["basic"] == 0


@pytest.mark.parametrize("dates", [{}, {"date_of_joining": "2026-03-01"}])
def test_no_payable_days_zeroes_pay_even_if_fixed_divisor_exceeds_the_month(dates):
    february = Period(2026, 2)
    versions = [company_setting("lop", {"divisor": 30})]
    employee = {**dates, "compensation": [salary("2025-04-01", 30000)]}
    assert compute_earnings(employee, february, versions, 28).regular_gross == 0


def test_lop_trace_uses_boundary_rate_and_average_employed_rate():
    employee = {"date_of_exit": "2025-07-20", "unpaid_days": "2.5",
                "compensation": [salary("2025-04-01", 30000), salary("2025-07-16", 60000)]}
    calc = EmployeeCalc(emp=employee, period=JULY, versions=[], company={})
    regular_pay(calc)
    assert calc.amounts["earned.basic"] == 21169
    cut = next(line for line in calc.lines if line.code == "lop_deduction").children[0]
    assert cut.amount == 24315
    assert [(term["monthly_rate"], term["days"]) for term in cut.inputs["terms"]] == [(60000, 11), (37500, Decimal("2.5"))]
    assert "(₹60,000 ÷ 31) × 11" in cut.formula
    assert "(₹37,500 ÷ 31) × 2.5" in cut.formula


def test_fractional_rounding_multiple_above_a_rupee_retains_the_fraction():
    assert round_amount(Decimal("452.50"), unit="2.50") == Decimal("452.50")
    rule = company_setting("lop", {"divisor": "days_in_month", "day_rate_rounding": {"unit": "2.50", "method": "nearest"}})
    employee = {"compensation": [salary("2025-04-01", "14027.50")]}
    assert compute_earnings(employee, JULY, [rule], 2).total_cut == 905


@pytest.mark.parametrize("period", [Period(2025, 2), Period(2024, 2), Period(2025, 4), JULY])
@pytest.mark.parametrize("setting", ["days_in_month", 26, 30])
def test_proration_matches_exact_daily_calculation_across_month_lengths(period, setting):
    day_count = period.days if setting == "days_in_month" else setting
    versions = [company_setting("lop", {"divisor": setting}, "2024-01-01")]
    windows = [(1, period.days), (5, period.days), (1, period.days - 4),
               (5, period.days - 4), (period.days // 2, period.days // 2)]
    revisions = [1, period.days // 2, period.days]
    unpaid_inputs = [Fraction(0), Fraction(1, 2), Fraction(9, 2), Fraction(period.days)]

    for (joined, left), raise_on, unpaid in product(windows, revisions, unpaid_inputs):
        employee = {
            "date_of_joining": period.start.replace(day=joined).isoformat(),
            "date_of_exit": period.start.replace(day=left).isoformat(),
            "compensation": [salary("2024-01-01", 30000), salary(period.start.replace(day=raise_on).isoformat(), 51000)],
        }
        # Extend the nearest employed day's salary for dates outside employment, then explicitly sum each day.
        rates = [51000 if max(joined, min(day, left)) >= raise_on else 30000 for day in range(1, period.days + 1)]
        employed_rates = rates[joined - 1:left]
        absent = sum(rates[:joined - 1]) + sum(rates[left:])
        leave = min(unpaid, len(employed_rates))
        full = int(Fraction(sum(rates), period.days) + Fraction(1, 2))
        unrounded_cut = (absent + leave * Fraction(sum(employed_rates), len(employed_rates))) / day_count
        cut = full if leave == len(employed_rates) else min(full, int(unrounded_cut + Fraction(1, 2)))

        result = compute_earnings(employee, period, versions, Decimal(unpaid.numerator) / unpaid.denominator)
        assert (result.contracted_gross, result.total_cut, result.regular_gross) == (full, cut, full - cut), (
            period, setting, joined, left, raise_on, unpaid
        )
