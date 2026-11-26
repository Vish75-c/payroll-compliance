"""Possible unpaid absences: working days with no check-in in the attendance and no approved leave.

They are only suggestions. A missing check-in can be a forgotten punch, a day at a client's site or work from
home, so nothing here changes pay: a maker either adds the days as an unpaid absence (the "Unpaid absence" change,
with a reason) or says they were not an absence (``dismiss``, with a reason). Both are in the month's history.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.db import transaction

from payroll_engine import policies as engine_policies
from payroll_engine.periods import Period

from ...shared.permissions import RuleViolation, require_role
from ..rules.policies import versions_for
from .events import record
from .models import AbsenceDismissal, ChangeRequest, PayrollRun

WEEKDAYS = engine_policies.WEEKDAYS
CLOSED_SHARE = 0.2  # fewer than this share of people checked in: a holiday or closed office, not everyone absent


def _days(first: date, last: date):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def _calendar(run: PayrollRun) -> dict:
    period = run.period_obj
    versions = [v for v in versions_for(run.company) if v["effective_from"][:10] <= period.start.isoformat()]
    return engine_policies.resolve_company(versions, "work_calendar", period).payload


def _handled_days(run: PayrollRun) -> tuple[dict[str, set[date]], set[str]]:
    """Days already entered by hand as unpaid absences, and people whose unpaid days were replaced by a figure."""
    days: dict[str, set[date]] = {}
    replaced: set[str] = set()
    for ch in ChangeRequest.objects.filter(company=run.company, period=run.period, status=ChangeRequest.CONFIRMED):
        for item in ch.proposal.get("items", []):
            code = item.get("employee_code")
            if item.get("type") == "unpaid_days":
                replaced.add(code)
            elif item.get("type") == "extra_unpaid_days":
                if item.get("dates"):
                    days.setdefault(code, set()).update(date.fromisoformat(x) for x in item["dates"])
                elif item.get("from"):
                    days.setdefault(code, set()).update(_days(date.fromisoformat(item["from"]), date.fromisoformat(item["to"])))
    return days, replaced


def _runs(days: list[date], working: list[date]) -> list[list[date]]:
    """Group days that follow each other on the working calendar (Friday and the next Monday are one run)."""
    position = {d: i for i, d in enumerate(working)}
    out: list[list[date]] = []
    for day in days:
        if out and position[day] == position[out[-1][-1]] + 1:
            out[-1].append(day)
        else:
            out.append([day])
    return out


def describe(dates: list[date]) -> str:
    first, last = dates[0], dates[-1]
    if len(dates) == 1:
        return f"{first:%d %b}"
    if len(dates) == (last - first).days + 1:
        return f"{first:%d}–{last:%d %b}"
    return ", ".join(f"{d:%d}" for d in dates[:-1]) + f" and {last:%d %b}"


def possible_absences(run: PayrollRun) -> dict:
    data = run.snapshot.data
    period = run.period_obj
    covered = data.get("presence_range")
    if not data.get("presence") or not covered:
        return {"status": "no_attendance", "absences": [], "no_check_in": [], "message":
                "The HR data in use has no attendance. Upload an attendance file (Employee, Date…) with the HR data to "
                "see days with no check-in and no approved leave."}
    first = max(period.start, date.fromisoformat(covered["from"]))
    last = min(period.end, date.fromisoformat(covered["to"]))
    covered_text = f"{date.fromisoformat(covered['from']):%d %b %Y} to {date.fromisoformat(covered['to']):%d %b %Y}"
    if first > last:
        return {"status": "not_this_month", "absences": [], "no_check_in": [],
                "message": f"The attendance in the HR data covers {covered_text}, not {period.label}."}
    calendar = _calendar(run)
    off = {WEEKDAYS.index(d) for d in calendar.get("weekly_off", [])}
    holidays = {date.fromisoformat(h) for h in calendar.get("holidays", [])}

    present: dict[str, set[int]] = {}
    for row in data["presence"]:
        if row["month"] == str(period):
            present.setdefault(row["employee_code"], set()).update(row["days"])
    on_leave: dict[str, set[date]] = {}
    for row in data.get("leave", []):  # only leave that counts (approved) is in the data; paid or unpaid
        start, end = date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"])
        on_leave.setdefault(row["employee_code"], set()).update(d for d in _days(max(start, first), min(end, last)))
    monthly_figure = {r["employee_code"] for r in data.get("attendance", []) if r["period"] == str(period)}
    handled, replaced = _handled_days(run)
    dismissed: dict[str, set[date]] = {}
    for x in AbsenceDismissal.objects.filter(company=run.company, period=run.period):
        dismissed.setdefault(x.employee_code, set()).update(date.fromisoformat(d) for d in x.dates)

    # A working day when almost nobody checked in is a holiday or a closed office missing from the calendar, not a
    # day everyone was absent: it is left out and named.
    closed = set()
    for day in _days(first, last):
        if day.weekday() in off or day in holidays:
            continue
        employed = [e for e in data["employees"] if e["code"] in present
                    and (not e.get("date_of_joining") or e["date_of_joining"] <= day.isoformat())
                    and (not e.get("date_of_exit") or e["date_of_exit"] >= day.isoformat())]
        if len(employed) >= 5 and sum(1 for e in employed if day.day in present[e["code"]]) < CLOSED_SHARE * len(employed):
            closed.add(day)

    # Working days of the month the attendance does not reach (a file ending on Friday the 29th before a weekend
    # still covers the month).
    uncovered = [d for d in _days(period.start, period.end)
                 if not first <= d <= last and d.weekday() not in off and d not in holidays]

    absences, no_check_in, skipped = [], [], []
    for emp in data["employees"]:
        code = emp["code"]
        start = max(first, date.fromisoformat(emp["date_of_joining"])) if emp.get("date_of_joining") else first
        end = min(last, date.fromisoformat(emp["date_of_exit"])) if emp.get("date_of_exit") else last
        if start > end:
            continue  # not employed while the attendance runs
        if code in monthly_figure or code in replaced:
            skipped.append({"employee_code": code, "name": emp.get("name", code)})
            continue  # unpaid days for the month are given as a figure, which replaces the leave and attendance
        working = [d for d in _days(start, end) if d.weekday() not in off and d not in holidays and d not in closed]
        expected = [d for d in working if d not in on_leave.get(code, set())]
        if not expected:
            continue
        if not present.get(code):
            # No check-in at all is more likely someone who does not use the attendance system than a month of absence.
            no_check_in.append({"employee_code": code, "name": emp.get("name", code), "working_days": len(expected),
                                "from": start.isoformat(), "to": end.isoformat()})
            continue
        missing = [d for d in expected if d.day not in present[code] and d not in handled.get(code, set())
                   and d not in dismissed.get(code, set())]
        for group in _runs(missing, working):
            absences.append({"employee_code": code, "name": emp.get("name", code), "dates": [d.isoformat() for d in group],
                             "days": len(group), "label": describe(group),
                             "suggested_reason": f"No check-in and no approved leave on {describe(group)} (attendance)"})
    return {
        "status": "ok", "absences": absences, "no_check_in": no_check_in, "figure_given": skipped,
        "checked": {"from": first.isoformat(), "to": last.isoformat(), "weekly_off": calendar.get("weekly_off", []),
                    "holidays": sorted(h.isoformat() for h in holidays if first <= h <= last),
                    "closed": sorted(d.isoformat() for d in closed)},
        "message": (f"Checked {first:%d %b} to {last:%d %b %Y}: working days (not {', '.join(d.capitalize() for d in calendar.get('weekly_off', [])) or 'any weekday'}"
                    f"{', not company holidays' if holidays else ''}) with no check-in and no approved leave."
                    + (f" Almost nobody checked in on {describe(sorted(closed))}, so {'it is' if len(closed) == 1 else 'they are'} "
                       "taken as a holiday: add company holidays on Rules & settings (Working days and holidays)." if closed else "")
                    + (f" The attendance covers {covered_text} only." if uncovered else "")),
    }


@transaction.atomic
def dismiss(run: PayrollRun, user, employee_code: str, dates: list[str], reason: str) -> AbsenceDismissal:
    require_role(user, "maker", "decide about possible absences")
    if run.is_locked:
        raise RuleViolation(f"{run.period_obj.label} is {run.get_status_display().lower()}; it cannot change.")
    reason = " ".join(str(reason or "").split())[:300]
    if not reason:
        raise RuleViolation("Say why these days are not an absence, for example \"forgot to punch; was at the client's site\".")
    period: Period = run.period_obj
    try:
        days = sorted({date.fromisoformat(str(d)) for d in dates})
    except ValueError:
        raise RuleViolation("Could not read the dates.") from None
    if not days or not all(period.start <= d <= period.end for d in days):
        raise RuleViolation(f"Give the dates, all within {period.label}.")
    emp = next((e for e in run.snapshot.data["employees"] if e["code"] == employee_code), None)
    if not emp:
        raise RuleViolation("Pick an employee from this month's HR data.")
    item = AbsenceDismissal.objects.create(company=run.company, period=run.period, employee_code=employee_code,
                                           dates=[d.isoformat() for d in days], reason=reason, dismissed_by=user)
    record(run, user, "absence_dismissed", f"Not an absence: {emp.get('name', employee_code)} ({employee_code}) on {describe(days)}. {reason}",
           employee_code=employee_code, dates=item.dates)
    return item
