"""The company's pay cycle: calendar month, attendance cutoff, or a shifted cycle; and the pay day setting the TDS due date."""
from datetime import date
from decimal import Decimal

import pytest

from payroll_engine import policies as P
from payroll_engine.engine import calculate_employee
from payroll_engine.periods import Period, cycle_window

from payroll.demo import setup
from payroll.features.dues import dues
from payroll.features.hr_data.normalize import unpaid_days_for
from payroll.features.pay_groups import service as pay_groups
from payroll.features.rules.policies import add_version
from payroll.shared.permissions import RuleViolation

from .conftest import company_setting


def cycle(mode, start_day):
    return company_setting("pay_cycle", {"mode": mode, "start_day": start_day})


def split(total):
    return {"basic": str(total * 50 // 100), "hra": str(total * 20 // 100), "special_allowance": str(total * 30 // 100)}


def ravi(unpaid, **kw):
    """₹60,000 a month, ₹66,000 from 21 Nov 2026."""
    emp = {"code": "RAVI", "name": "Ravi", "work_state": "KA", "date_of_joining": "2024-06-01",
           "compensation": [{"effective_from": "2024-06-01", "components": split(60000)},
                            {"effective_from": "2026-11-21", "components": split(66000)}],
           "unpaid_days": unpaid, "one_time": [], "ytd": {"months": []}, "arrears_basis": []}
    emp.update(kw)
    return emp


def amounts(emp, versions, period="2026-11"):
    return {k: Decimal(v) for k, v in calculate_employee(emp, Period.parse(period), versions, {"state": "KA"})["amounts"].items()}


# --------------------------------------------------------------------------- the dates

def test_cycle_window():
    w = cycle_window(Period(2026, 11), 26)
    assert (w.start, w.end, w.days, w.label) == (date(2026, 10, 26), date(2026, 11, 25), 31, "26 Oct to 25 Nov 2026")
    assert (cycle_window(Period(2026, 1), 8).start, cycle_window(Period(2026, 1), 8).end) == (date(2025, 12, 8), date(2026, 1, 7))
    assert cycle_window(Period(2026, 3), 28).start == date(2026, 2, 28) and cycle_window(Period(2026, 3), 28).days == 28
    calendar = cycle_window(Period(2026, 2), 1)
    assert (calendar.start, calendar.end, calendar.days) == (date(2026, 2, 1), date(2026, 2, 28), 28)


def test_windows_follow_the_mode(statutory_versions):
    nov = Period(2026, 11)
    assert P.salary_window(statutory_versions, nov).days == P.attendance_window(statutory_versions, nov).days == 30
    cutoff = statutory_versions + [cycle("attendance_cutoff", 26)]
    assert P.salary_window(cutoff, nov).start == date(2026, 11, 1)
    assert P.attendance_window(cutoff, nov).start == date(2026, 10, 26)
    shifted = statutory_versions + [cycle("shifted", 26)]
    assert P.salary_window(shifted, nov).start == P.attendance_window(shifted, nov).start == date(2026, 10, 26)


@pytest.mark.parametrize("payload,ok", [
    ({"mode": "calendar", "start_day": 1}, True),
    ({"mode": "shifted", "start_day": 26}, True),
    ({"mode": "attendance_cutoff", "start_day": 2}, True),
    ({"mode": "shifted", "start_day": 29}, False),  # not every month has a 29th
    ({"mode": "shifted", "start_day": 1}, False),
    ({"mode": "shifted"}, False),
    ({"mode": "weekly", "start_day": 5}, False),
])
def test_pay_cycle_setting_is_checked(payload, ok):
    assert (P.validate_payload("pay_cycle", payload) == []) is ok


# --------------------------------------------------------------------------- the calculation

def test_calendar_month_is_unchanged(statutory_versions):
    a = amounts(ravi("1"), statutory_versions + [cycle("calendar", 1)])
    assert (a["fixed_gross"], a["lop_deduction"], a["gross"], a["days_in_month"]) == (62000, 2066, 59934, 30)


def test_attendance_cutoff_keeps_the_calendar_salary(statutory_versions):
    # 27-28 Oct fall in November's attendance cycle (26 Oct to 25 Nov); the salary is still 1-30 Nov
    a = amounts(ravi("2"), statutory_versions + [cycle("attendance_cutoff", 26)])
    assert (a["fixed_gross"], a["lop_deduction"], a["gross"], a["net_pay"], a["days_in_month"]) == (62000, 4134, 57866, 55866, 30)


def test_shifted_cycle_pays_the_cycle_dates(statutory_versions):
    # 26 Oct to 25 Nov: 26 days at ₹60,000 and 5 at ₹66,000, out of 31; one day's pay is ÷ 31
    a = amounts(ravi("2"), statutory_versions + [cycle("shifted", 26)])
    assert (a["fixed_gross"], a["lop_deduction"], a["gross"], a["net_pay"], a["days_in_month"]) == (60968, 3934, 57034, 55034, 31)


@pytest.mark.parametrize("divisor,cut", [("days_in_month", 3934), (30, 4064), (26, 4690)])
def test_day_count_setting_works_with_a_shifted_cycle(statutory_versions, divisor, cut):
    versions = statutory_versions + [cycle("shifted", 26), company_setting("lop", {"divisor": divisor})]
    assert amounts(ravi("2"), versions)["lop_deduction"] == cut


def test_joining_inside_a_shifted_cycle(statutory_versions):
    # joined 1 Nov: 26-31 Oct (6 days) of November's cycle are not paid
    r = calculate_employee(ravi("0", date_of_joining="2026-11-01"), Period(2026, 11),
                           statutory_versions + [cycle("shifted", 26)], {"state": "KA"})
    assert Decimal(r["amounts"]["not_employed_days"]) == 6
    assert Decimal(r["amounts"]["lop_deduction"]) == 11613  # 5,806 + 2,323 + 3,484: the 6 days before joining are cut at the ₹60,000 in force on them
    assert any(ref["key"] == "pay_cycle" for line in r["lines"] for ref in line["rules"])


def test_leave_is_split_by_the_attendance_cycle():
    data = {"attendance": [], "leave": [{"employee_code": "E1", "unpaid": True, "start_date": "2026-11-24",
                                         "end_date": "2026-11-27", "days": "4"}]}
    nov, dec = Period(2026, 11), Period(2026, 12)
    assert unpaid_days_for(data, "E1", nov) == 4
    assert unpaid_days_for(data, "E1", nov, cycle_window(nov, 26)) == 2  # 24-25 Nov
    assert unpaid_days_for(data, "E1", dec, cycle_window(dec, 26)) == 2  # 26-27 Nov count in December


# --------------------------------------------------------------------------- the pay day and TDS

def test_pay_date():
    assert dues.pay_date(date(2026, 11, 30), 7) == date(2026, 12, 7)
    assert dues.pay_date(date(2026, 11, 30), 30) == date(2026, 11, 30)
    assert dues.pay_date(date(2026, 2, 28), 31) == date(2026, 2, 28)
    assert dues.pay_date(date(2026, 11, 25), 28) == date(2026, 11, 28)  # shifted cycle paid in the same month


def test_tds_is_due_after_the_month_salary_is_paid(statutory_versions):
    tds = lambda period, pay_day, versions=statutory_versions: dues._due_day(versions, "income_tax", period, "IN", pay_day)
    assert tds(Period(2026, 11), None) == date(2026, 12, 7)   # no pay day: as before
    assert tds(Period(2026, 11), 7) == date(2027, 1, 7)       # paid 7 Dec: deducted in December
    assert tds(Period(2026, 11), 30) == date(2026, 12, 7)     # paid 30 Nov
    assert tds(Period(2027, 2), 7) == date(2027, 4, 30)       # paid 7 Mar: March deductions are due 30 April
    assert tds(Period(2027, 3), 7) == date(2027, 5, 7)        # March salary paid 7 Apr: deducted in April
    shifted = statutory_versions + [cycle("shifted", 26)]
    assert tds(Period(2026, 11), 28, shifted) == date(2026, 12, 7)  # cycle ends 25 Nov, paid 28 Nov
    # PF is tied to the wage month, whenever salary is paid
    assert dues._due_day(statutory_versions, "pf", Period(2026, 11), "IN", 7) == date(2026, 12, 15)


# --------------------------------------------------------------------------- a company's months

pytestmark_db = pytest.mark.django_db


def _set_cycle(company, mode, day, month=None):
    """The company's cycle is its default pay group's ("Everyone else")."""
    return pay_groups.change_cycle(pay_groups.ensure_default(company), setup.system_user("maker"),
                                   {"mode": mode, "start_day": day, "from": month})


@pytestmark_db
def test_attendance_cutoff_moves_late_unpaid_leave_to_the_next_month(demo):
    company, _ = demo
    _set_cycle(company, "attendance_cutoff", 17)  # June counts unpaid days from 17 May to 16 Jun
    june = setup.run_month(company, "2025-06", finalize=False, log=lambda *a: None)
    unpaid = {e["code"]: Decimal(e["unpaid_days"]) for e in june.input["employees"]}
    days = {e["code"]: Decimal(e["amounts"]["days_in_month"]) for e in june.result["employees"]}
    assert unpaid["BL075"] == 1 and unpaid["BL005"] == 1
    assert unpaid["BL074"] == 0  # 18-19 June is after the cutoff: it counts in July
    assert days["BL074"] == 30  # salary is still for the calendar month


@pytestmark_db
def test_shifted_cycle_month(demo):
    company, _ = demo
    _set_cycle(company, "shifted", 17)  # June is 17 May to 16 Jun, 31 days
    june = setup.run_month(company, "2025-06", finalize=False, log=lambda *a: None)
    days = {e["code"]: Decimal(e["amounts"]["days_in_month"]) for e in june.result["employees"]}
    assert set(days.values()) == {31}
    unpaid = {e["code"]: Decimal(e["unpaid_days"]) for e in june.input["employees"]}
    assert unpaid["BL074"] == 0 and unpaid["BL075"] == 1


@pytestmark_db
def test_a_cycle_change_cannot_reach_into_a_final_month(demo):
    company, _ = demo
    setup.run_month(company, "2025-04", log=lambda *a: None)
    with pytest.raises(RuleViolation, match="April 2025 is final"):
        _set_cycle(company, "shifted", 26, "2025-04")
    _set_cycle(company, "shifted", 26, "2025-05")  # from the next month: May is a transition period
    # the old company setting points to the pay groups
    with pytest.raises(RuleViolation, match="per pay group"):
        add_version(company=company, key="pay_cycle", jurisdiction="", effective_from=date(2025, 6, 1),
                    payload={"mode": "shifted", "start_day": 26}, title="", note="", user=setup.system_user("maker"))


@pytestmark_db
def test_pay_day_sets_the_tds_due_date_of_a_final_month(demo):
    company, _ = demo
    company.pay_day = 7
    company.save(update_fields=["pay_day"])
    april = setup.run_month(company, "2025-04", log=lambda *a: None)
    owed = {x.kind: x for x in april.dues.all()}
    assert owed["tds"].due_date == date(2025, 6, 7)  # April salary paid 7 May
    assert owed["pf"].due_date == date(2025, 5, 15)
    assert april.input["company"]["pay_day"] == 7
