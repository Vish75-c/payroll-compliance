"""Pay groups: people on different cycles in one month, where each person's group comes from, and a cycle change
paid as a transition period (no day paid twice or skipped)."""
from datetime import date
from decimal import Decimal

import pytest

from payroll_engine.engine import calculate_employee
from payroll_engine.money import round_amount
from payroll_engine.periods import Period

from payroll.demo import setup
from payroll.features.dues import dues
from payroll.features.pay_groups import service as pay_groups
from payroll.features.pay_groups.models import EmployeePayGroup
from payroll.features.payroll_runs.cycle import month_windows
from payroll.shared.permissions import RuleViolation

from .test_pay_cycle import split


def maker():
    return setup.system_user("maker")


def window(salary_from, salary_to, basis, **kw):
    return {"group": "g", "group_name": "G", "salary_from": salary_from, "salary_to": salary_to, "basis_days": basis,
            "attendance_from": salary_from, "attendance_to": salary_to, "pay_day": None, **kw}


def emp(pay_window, unpaid="0"):
    return {"code": "E1", "name": "E1", "work_state": "KA", "date_of_joining": "2020-01-01",
            "compensation": [{"effective_from": "2020-01-01", "components": split(60000)}],
            "unpaid_days": unpaid, "one_time": [], "ytd": {"months": []}, "arrears_basis": [], "pay_window": pay_window}


# --------------------------------------------------------------------------- the calculation

@pytest.mark.parametrize("pw,pay", [
    (window("2027-01-01", "2027-01-25", 31, transition=True), 48387),   # day 26 from January: 1-25 Jan, 25 of 31 days
    (window("2026-12-26", "2027-01-31", 31, transition=True), 71613),   # back to the calendar: 26 Dec - 31 Jan, 37 of 31
    (window("2027-01-01", "2027-01-31", 31), 60000),                    # a normal month
])
def test_a_transition_period_is_paid_for_the_days_it_covers(statutory_versions, pw, pay):
    r = calculate_employee(emp(pw), Period(2027, 1), statutory_versions, {"state": "KA"})
    assert Decimal(r["amounts"]["fixed_gross"]) == pay
    assert r["pay_window"]["salary_from"] == pw["salary_from"]


def test_unpaid_days_in_a_transition_are_cut_at_the_normal_day_rate(statutory_versions):
    r = calculate_employee(emp(window("2027-01-01", "2027-01-25", 31, transition=True), "2"), Period(2027, 1),
                           statutory_versions, {"state": "KA"})
    a = {k: Decimal(v) for k, v in r["amounts"].items()}
    assert (a["fixed_gross"], a["lop_deduction"], a["gross"], a["basis_days"], a["days_in_month"]) == (48387, 3870, 44517, 31, 25)
    line = next(x for x in r["lines"] if x["code"] == "earned.basic")
    assert "× 25 ÷ 31 days" in line["formula"]


# --------------------------------------------------------------------------- whose group

pytestmark_db = pytest.mark.django_db


def _factory(company, month=None, **cycle):
    return pay_groups.create_group(company, maker(), {"name": "Factory", "mode": "shifted", "start_day": 26, "pay_day": 28,
                                                      "covers": {"departments": ["design"]}, "from": month, **cycle})


@pytestmark_db
def test_who_is_in_which_group(demo):
    company, snapshot = demo
    _factory(company)  # covers the Design department (any case)
    people = {e["code"]: e for e in snapshot.data["employees"]}
    windows = month_windows(company, Period(2025, 6), list(people.values()))
    design = [c for c, e in people.items() if e.get("department") == "Design"]
    assert design and all(windows[c].group == "factory" and windows[c].source == "rule" for c in design)
    assert windows["BL007"].group == "default" and windows["BL007"].source == "default"
    assert windows[design[0]].reason == "Department: Design"

    # a "Pay group" column beats the rules; an unknown name falls back to them
    employees = [dict(e, pay_group="FACTORY") if c == "BL007" else dict(e, pay_group="Plant") if c == design[0] else e
                 for c, e in people.items()]
    assert pay_groups.unknown_group_values(employees, pay_groups.groups_for(company, Period(2025, 6))) == ["Plant"]
    pay_groups.refresh_from_data(company, employees)
    windows = month_windows(company, Period(2025, 6), employees)
    assert (windows["BL007"].group, windows["BL007"].source) == ("factory", "hr_data")
    assert (windows[design[0]].group, windows[design[0]].source) == ("factory", "rule")

    # set by hand beats the data, and the data never replaces it
    pay_groups.assign(company, maker(), ["BL007"], "default", "2025-06", "Head office after all")
    pay_groups.refresh_from_data(company, employees)
    assert month_windows(company, Period(2025, 6), employees)["BL007"].source == "manual"
    # clearing it goes back to what the reviewed data says (no "Pay group" column there; Leadership: the default group)
    pay_groups.clear_override(company, maker(), ["BL007"], "2025-06")
    cleared = month_windows(company, Period(2025, 6), employees)["BL007"]
    assert (cleared.group, cleared.source) == ("default", "default")


@pytestmark_db
def test_two_groups_in_one_month(demo):
    company, _ = demo
    _factory(company)
    company.pay_day = 1
    company.save(update_fields=["pay_day"])
    june = setup.run_month(company, "2025-06", log=lambda *a: None)
    people = {e["code"]: e for e in june.result["employees"]}
    design = [c for c, e in people.items() if e["pay_window"]["group"] == "factory"]
    others = [c for c in people if c not in design]
    assert design and others
    assert {people[c]["pay_window"]["salary_from"] for c in design} == {"2025-05-26"}  # 26 May - 25 Jun
    assert {people[c]["amounts"]["days_in_month"] for c in design} == {"31"}
    assert {people[c]["pay_window"]["salary_from"] for c in others} == {"2025-06-01"}
    groups = {g["group"]: g for g in june.result["totals_by_group"]}
    assert groups["factory"]["pay_date"] == "2025-06-28" and groups["default"]["pay_date"] == "2025-07-01"
    assert sum(Decimal(g["totals"]["net_pay"]) for g in groups.values()) == Decimal(june.result["totals"]["net_pay"])
    # a net pay file per group, checked against the register when the month was finalised
    assert {"net_pay_factory.csv", "net_pay_default.csv"} <= set(june.files["files"])
    # TDS: Factory is paid on 28 June (deducted in June, due 7 July), the rest on 1 July (due 7 August)
    tds = {x.jurisdiction: x for x in june.dues.filter(kind="tds")}
    deducted = {dues._deducted_in(people[c]) for c in people if Decimal(people[c]["amounts"]["tds"])}
    assert set(tds) == (deducted if len(deducted) > 1 else {""})
    if "2025-06" in tds:
        assert tds["2025-06"].due_date == date(2025, 7, 7) and tds["2025-07"].due_date == date(2025, 8, 7)


@pytestmark_db
def test_a_cycle_change_is_paid_as_a_transition_period(demo):
    company, _ = demo
    setup.run_month(company, "2025-04", log=lambda *a: None)  # April final, calendar month
    with pytest.raises(RuleViolation, match="April 2025 is final"):
        _factory(company, "2025-04")
    _factory(company, "2025-05")
    may = setup.run_month(company, "2025-05", log=lambda *a: None)
    people = {e["code"]: e for e in may.result["employees"]}
    design = [c for c, e in people.items() if e["pay_window"]["group"] == "factory"]
    pw = people[design[0]]["pay_window"]
    # April paid up to 30 Apr, so May on the new cycle is 1-25 May: 25 days of a 30-day cycle (26 Apr - 25 May)
    assert (pw["salary_from"], pw["salary_to"], pw["basis_days"], pw["transition"]) == ("2025-05-01", "2025-05-25", 30, True)
    a = people[design[0]]["amounts"]
    assert Decimal(a["fixed_gross"]) == sum(round_amount(Decimal(v) * 25 / 30) for k, v in a.items() if k.startswith("rate."))
    others = [c for c in people if c not in design]
    assert people[others[0]]["pay_window"]["transition"] is False
    # the month after is a normal cycle: 26 May - 25 Jun
    june = setup.run_month(company, "2025-06", finalize=False, log=lambda *a: None)
    pw = next(e for e in june.result["employees"] if e["code"] == design[0])["pay_window"]
    assert (pw["salary_from"], pw["salary_to"], pw["transition"]) == ("2025-05-26", "2025-06-25", False)
    # people cannot be moved into a month already final
    with pytest.raises(RuleViolation, match="May 2025 is final"):
        pay_groups.assign(company, maker(), [design[0]], "default", "2025-05")


@pytestmark_db
def test_no_transition_when_the_month_before_is_not_final(demo):
    company, _ = demo
    _factory(company)
    may = month_windows(company, Period(2025, 5), [{"code": "BL001", "department": "Design"}])["BL001"]
    assert (may.salary.start, may.salary.end, may.transition) == (date(2025, 4, 26), date(2025, 5, 25), False)


@pytestmark_db
def test_rows_from_the_data_are_dated_from_the_first_open_month(demo):
    company, snapshot = demo
    setup.run_month(company, "2025-04", log=lambda *a: None)
    _factory(company)
    rows = EmployeePayGroup.objects.filter(company=company, group__code="factory")
    assert rows.exists() and {r.effective_from for r in rows} == {date(2025, 5, 1)}
