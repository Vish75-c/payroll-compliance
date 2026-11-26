"""The dates each person's month covers: their pay group's cycle, and a transition period when it changed.

A month is worked out per person. Their pay group (``pay_groups``) gives the cycle: a calendar month, a calendar
month with unpaid days counted on a cycle, or a shifted cycle (26 Oct to 25 Nov). When the month before is final and
ended on another day than this cycle expects (the cycle changed, or the person moved group), this month is a
transition period: from the day after the last paid day to the end of the new cycle, paid for the days it covers.
The calculation gets these dates in the month's input (``pay_window``), so a stored month always recalculates the same.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from payroll_engine import policies as engine_policies
from payroll_engine.periods import Period, Window, cycle_window, pay_date, parse_date

from ..companies.models import Company
from ..pay_groups import service as groups_service
from .models import PayrollRun


@dataclass
class PersonWindow:
    group: str
    group_name: str
    default: bool
    source: str
    reason: str
    salary: Window
    attendance: Window
    basis_days: int
    pay_day: int | None
    transition: bool = False
    notes: list[str] = field(default_factory=list)

    def as_input(self) -> dict:
        return {"group": self.group, "group_name": self.group_name, "default": self.default, "group_source": self.source,
                "group_reason": self.reason, "salary_from": self.salary.start.isoformat(), "salary_to": self.salary.end.isoformat(),
                "basis_days": self.basis_days, "attendance_from": self.attendance.start.isoformat(),
                "attendance_to": self.attendance.end.isoformat(), "pay_day": self.pay_day, "transition": self.transition}


def normal_windows(payload: dict, period: Period) -> tuple[Window, Window]:
    """(salary, attendance) dates of ``period`` under a cycle, with no transition."""
    mode, day = payload.get("mode", "calendar"), int(payload.get("start_day") or 1)
    calendar = cycle_window(period, 1)
    if mode == "shifted":
        w = cycle_window(period, day)
        return w, w
    if mode == "attendance_cutoff":
        return calendar, cycle_window(period, day)
    return calendar, calendar


def _previous_ends(company: Company, period: Period) -> dict[str, tuple]:
    """Where each person's last month ended, when the month before ``period`` is final: {code: (salary_to, attendance_to)}."""
    prev = period.prev()
    run = PayrollRun.objects.filter(company=company, period=str(prev), run_type=PayrollRun.TYPE_REGULAR,
                                    status=PayrollRun.FINALIZED).first()
    if not run or not run.input:
        return {}
    versions = run.input.get("policy_versions", [])
    out = {}
    for e in run.input.get("employees", []):
        pw = e.get("pay_window")
        if pw:
            out[e["code"]] = (parse_date(pw["salary_to"]), parse_date(pw.get("attendance_to") or pw["salary_to"]))
        else:  # a month stored before pay groups: its dates come from the pay cycle setting it was calculated with
            out[e["code"]] = (engine_policies.salary_window(versions, prev).end, engine_policies.attendance_window(versions, prev).end)
    return out


def month_windows(company: Company, period: Period, employees: list[dict]) -> dict[str, PersonWindow]:
    groups = groups_service.groups_for(company, period)
    rows = groups_service.rows_as_of(company, period)
    previous = _previous_ends(company, period)
    out = {}
    for emp in employees:
        g, source, reason = groups_service.group_of(emp, groups, rows)
        salary, attendance = normal_windows(g.payload, period)
        basis = salary.days
        transition = False
        notes = []
        if emp["code"] in previous:
            prev_salary, prev_attendance = previous[emp["code"]]
            if prev_salary + timedelta(days=1) != salary.start:
                salary = Window(prev_salary + timedelta(days=1), salary.end)
                transition = True
                notes.append(f"transition: {salary.label} ({salary.days} of {basis} days)")
            if prev_attendance + timedelta(days=1) != attendance.start:
                attendance = Window(prev_attendance + timedelta(days=1), attendance.end)
                transition = True
        out[emp["code"]] = PersonWindow(g.group.code, g.group.name, g.group.is_default, source, reason, salary, attendance,
                                        basis, g.pay_day, transition, notes)
    return out


def window_for(company: Company, period: Period, code: str, data: dict | None) -> PersonWindow:
    """One person's dates in ``period``: as stored, when that month is final, else worked out now."""
    run = PayrollRun.objects.filter(company=company, period=str(period), run_type=PayrollRun.TYPE_REGULAR,
                                    status=PayrollRun.FINALIZED).first()
    stored = next((e.get("pay_window") for e in (run.input or {}).get("employees", []) if e["code"] == code), None) if run else None
    if stored:
        return PersonWindow(stored["group"], stored["group_name"], bool(stored.get("default")), stored.get("group_source", ""),
                            stored.get("group_reason", ""), Window(parse_date(stored["salary_from"]), parse_date(stored["salary_to"])),
                            Window(parse_date(stored["attendance_from"]), parse_date(stored["attendance_to"])),
                            int(stored["basis_days"]), stored.get("pay_day"), bool(stored.get("transition")))
    emp = next((e for e in (data or {}).get("employees", []) if e["code"] == code), {"code": code})
    return month_windows(company, period, [emp])[code]


def default_windows(company: Company, period: Period) -> tuple[Window, Window]:
    """The default group's normal dates in ``period`` (for the month's day count before people are known)."""
    g = next(x for x in groups_service.groups_for(company, period) if x.group.is_default)
    return normal_windows(g.payload, period)


def is_calendar(window: Window, period: Period) -> bool:
    return (window.start, window.end) == (period.start, period.end)


def month_text(window: Window, period: Period) -> str:
    """'November 2026', or 'November 2026 (26 Oct to 25 Nov 2026)' when the dates are not the calendar month."""
    return period.label if is_calendar(window, period) else f"{period.label} ({window.label})"


def _w(w: Window, period: Period) -> dict:
    return {"from": w.start.isoformat(), "to": w.end.isoformat(), "days": w.days, "label": w.label, "calendar": is_calendar(w, period)}


def describe(company: Company, period: Period, employees: list[dict]) -> dict:
    """The month's dates for the screens: each group's normal dates and pay date, and how many people are in a
    transition. ``salary``/``attendance`` are the default group's (as before pay groups)."""
    windows = month_windows(company, period, employees)
    groups = []
    for g in groups_service.groups_for(company, period):
        salary, attendance = normal_windows(g.payload, period)
        people = [w for w in windows.values() if w.group == g.group.code]
        if not people and not g.group.is_default:
            continue
        groups.append({"code": g.group.code, "name": g.group.name, "default": g.group.is_default, "people": len(people),
                       "transition": sum(1 for w in people if w.transition), "salary": _w(salary, period),
                       "attendance": _w(attendance, period),
                       "pay_date": pay_date(salary.end, g.pay_day).isoformat() if g.pay_day else ""})
    default = groups[0]
    return {"salary": default["salary"], "attendance": default["attendance"], "groups": groups}
