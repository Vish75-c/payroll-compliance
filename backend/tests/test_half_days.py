"""Half days, the way Horilla records leave: whether the first and the last day are a full day or a half (Start Date
Breakdown / End Date Breakdown). Every day counts 1, a half day 0.5; the cut is one day's pay times the total."""
from datetime import date
from decimal import Decimal

import pytest

from payroll_engine.engine import calculate_employee
from payroll_engine.periods import Period

from payroll.features.hr_data.canonical import heuristic_mapping
from payroll.features.hr_data.normalize import build_snapshot, default_value_maps, unpaid_days_for
from payroll.features.hr_data.profiles import match_layout

from .test_pay_cycle import split

HORILLA = ["Employee", "Start Date", "Start Date Breakdown", "End Date", "End Date Breakdown", "Requested Days", "Status", "Leave Type"]


def leave_rows(*rows):
    return [dict(zip(HORILLA, ["Ravi Rao (E1)", *r[:2], r[2], r[3], r[4], "Approved", "Loss of Pay"])) for r in rows]


def build(rows):
    mapping = {"Employee": "employee.name", "Start Date": "leave.start_date", "Start Date Breakdown": "leave.start_breakdown",
               "End Date": "leave.end_date", "End Date Breakdown": "leave.end_breakdown", "Requested Days": "leave.days",
               "Status": "leave.status", "Leave Type": "leave.type"}
    staff = {"name": "Staff", "headers": ["Emp Code", "Name"], "rows": [{"Emp Code": "E1", "Name": "Ravi Rao"}],
             "mapping": {"Emp Code": "employee.code", "Name": "employee.name"}, "value_maps": {}, "options": {}, "dataset_hint": "employees"}
    leave = {"name": "Leave", "headers": HORILLA, "rows": rows, "mapping": mapping,
             "value_maps": default_value_maps(rows, mapping), "options": {}}
    return build_snapshot([staff, leave], {"components": [{"code": "basic", "share": "0.5"}, {"code": "hra", "share": "0.2"},
                                                         {"code": "special_allowance", "share": "remainder"}]}, date(2026, 11, 1))


def test_a_half_day_counts_half():
    data, _ = build(leave_rows(("2026-11-10", "First Half", "2026-11-10", "First Half", "0.5")))
    assert data["leave"][0]["day_values"] == [{"date": "2026-11-10", "value": "0.5"}]
    assert unpaid_days_for(data, "E1", Period(2026, 11)) == Decimal("0.5")


def test_a_half_last_day_stays_on_its_date_across_a_month_end():
    # 29 and 30 Sep full, 1 Oct the first half: 2.5 days, 2 in September and 0.5 in October (not 1.67 and 0.83)
    data, _ = build(leave_rows(("2026-09-29", "Full Day", "2026-10-01", "First Half", "2.5")))
    values = {x["date"]: x["value"] for x in data["leave"][0]["day_values"]}
    assert values == {"2026-09-29": "1", "2026-09-30": "1", "2026-10-01": "0.5"}
    assert unpaid_days_for(data, "E1", Period(2026, 9)) == 2
    assert unpaid_days_for(data, "E1", Period(2026, 10)) == Decimal("0.5")


def test_weekly_offs_are_left_out_when_the_hr_tool_left_them_out():
    # Friday 27 Nov to Monday 30 Nov, 2 days requested: Saturday and Sunday were not counted by the HR tool
    data, checks = build(leave_rows(("2026-11-27", "Full Day", "2026-11-30", "Full Day", "2")))
    assert [x["date"] for x in data["leave"][0]["day_values"]] == ["2026-11-27", "2026-11-30"]
    assert not [c for c in checks if c["code"] == "leave_days_by_date"]


def test_days_that_cannot_be_matched_to_dates_fall_back_with_a_warning():
    data, checks = build(leave_rows(("2026-11-10", "Full Day", "2026-11-12", "Full Day", "1.5")))
    assert "day_values" not in data["leave"][0]
    assert [c for c in checks if c["code"] == "leave_days_by_date"]
    assert unpaid_days_for(data, "E1", Period(2026, 11)) == Decimal("1.5")


def test_full_days_need_no_dates():
    data, _ = build(leave_rows(("2026-11-10", "Full Day", "2026-11-11", "Full Day", "2")))
    assert "day_values" not in data["leave"][0]


def test_breakdown_values_are_offered_for_confirmation():
    rows = leave_rows(("2026-11-10", "First Half", "2026-11-10", "Second Half", "0.5"), ("2026-11-12", "Full Day", "2026-11-12", "Full Day", "1"))
    maps = default_value_maps(rows, {"Start Date Breakdown": "leave.start_breakdown", "End Date Breakdown": "leave.end_breakdown"})
    assert maps["leave.breakdown"] == {"First Half": "half", "Second Half": "half", "Full Day": "full"}


def test_two_and_a_half_unpaid_days_cut_two_and_a_half_days_pay(statutory_versions):
    emp = {"code": "E1", "name": "E1", "work_state": "KA", "compensation": [{"effective_from": "2020-01-01", "components": split(60000)}],
           "unpaid_days": "2.5", "one_time": [], "ytd": {"months": []}, "arrears_basis": []}
    a = calculate_employee(emp, Period(2026, 11), statutory_versions, {"state": "KA"})["amounts"]
    assert Decimal(a["lop_deduction"]) == 5000  # ₹60,000 ÷ 30 × 2.5


def test_horilla_leave_export_maps_the_breakdown_columns():
    match = match_layout(HORILLA + ["Description"], "leave", "horilla")
    assert match and match.mapping["Start Date Breakdown"] == "leave.start_breakdown"
    assert match.mapping["End Date Breakdown"] == "leave.end_breakdown"
    found = heuristic_mapping(["Emp Code", "Leave From", "Leave To", "Start Date Breakdown", "End Date Breakdown", "Days"])
    assert found["Start Date Breakdown"]["target"] == "leave.start_breakdown"
    assert found["End Date Breakdown"]["target"] == "leave.end_breakdown"


@pytest.mark.parametrize("value,half", [("First Half", True), ("second_half", True), ("HD", True), ("Full Day", False), ("full_day", False)])
def test_guess_half(value, half):
    from payroll.features.hr_data.values import guess_half
    assert guess_half(value) is half
