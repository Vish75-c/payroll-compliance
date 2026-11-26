from __future__ import annotations

from datetime import date

from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from payroll_engine.periods import Period

from payroll.shared.permissions import RuleViolation
from payroll.shared.present import _dt, _user

from ..companies.models import Company
from ..payroll_runs.cycle import month_windows, normal_windows
from . import service
from .models import FROM_THE_START, EmployeePayGroup, PayGroup, PayGroupCycle


def _month(value) -> str:
    return "" if value == FROM_THE_START else str(Period.of(value))


def _cycle(c: PayGroupCycle, company: Company) -> dict:
    return {"id": c.pk, "from": _month(c.effective_from), "mode": c.mode, "start_day": c.start_day,
            "pay_day": c.pay_day, "company_pay_day": company.pay_day if c.group.is_default else None,
            "covers": c.covers or {}, "note": c.note, "created_by": _user(c.created_by), "created_at": _dt(c.created_at)}


def _period(request) -> Period:
    try:
        return Period.parse(request.query_params.get("period") or str(service.first_open_period_or_today(request.company)))
    except ValueError as exc:
        raise RuleViolation(str(exc)) from exc


@api_view(["GET", "POST"])
def pay_groups(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    if request.method == "POST":
        group = service.create_group(company, request.user, request.data)
        return Response({"id": group.pk, "code": group.code}, status=201)
    request.company = company
    period = _period(request)
    service.ensure_default(company)
    employees = service.latest_employees(company)
    windows = month_windows(company, period, employees) if employees else {}
    first_open = service.first_open_period(company)
    out = []
    for g in PayGroup.objects.filter(company=company).prefetch_related("cycles"):
        cycles = list(g.cycles.all())
        now = next((x for x in service.groups_for(company, period) if x.group.pk == g.pk), None)
        salary, attendance = normal_windows(now.payload, period) if now else (None, None)
        out.append({
            "id": g.pk, "code": g.code, "name": g.name, "default": g.is_default,
            "cycles": [_cycle(c, company) for c in cycles],
            "now": _cycle(now.cycle, company) if now else None,
            "dates": {"salary": salary.label, "attendance": attendance.label} if now else None,
            "people": sum(1 for w in windows.values() if w.group == g.code),
        })
    suggestions = {"departments": sorted({e["department"] for e in employees if e.get("department")}),
                   "work_states": sorted({e["work_state"] for e in employees if e.get("work_state")}),
                   "designations": sorted({e["designation"] for e in employees if e.get("designation")})}
    return Response({"period": str(period), "first_open": str(first_open) if first_open else "", "groups": out,
                      "suggestions": suggestions})


@api_view(["POST"])
def pay_group_cycles(request, group_id):
    group = get_object_or_404(PayGroup, pk=group_id)
    cycle = service.change_cycle(group, request.user, request.data)
    return Response(_cycle(cycle, group.company), status=201)


@api_view(["GET"])
def employees(request, company_id):
    """People in the latest reviewed HR data, with their pay group in a month and where it came from."""
    company = get_object_or_404(Company, pk=company_id)
    request.company = company
    period = _period(request)
    people = service.latest_employees(company)
    windows = month_windows(company, period, people) if people else {}
    history: dict[str, list] = {}
    for row in EmployeePayGroup.objects.filter(company=company).select_related("group", "created_by").order_by("effective_from", "id"):
        history.setdefault(row.employee_code, []).append({
            "id": row.pk, "from": _month(row.effective_from), "group": row.group.code, "group_name": row.group.name,
            "source": row.source, "reason": row.reason, "note": row.note, "by": _user(row.created_by), "at": _dt(row.created_at)})
    rows = []
    for e in people:
        w = windows[e["code"]]
        upcoming = [h for h in history.get(e["code"], []) if h["from"] and h["from"] > str(period)]
        rows.append({"code": e["code"], "name": e.get("name", e["code"]), "department": e.get("department", ""),
                     "designation": e.get("designation", ""), "work_state": e.get("work_state", ""),
                     "date_of_joining": e.get("date_of_joining"), "date_of_exit": e.get("date_of_exit"),
                     "pay_group_column": e.get("pay_group", ""),
                     "group": w.group, "group_name": w.group_name, "source": w.source, "reason": w.reason,
                     "salary": w.salary.label, "salary_days": w.salary.days, "basis_days": w.basis_days,
                     "attendance": w.attendance.label if (w.attendance.start, w.attendance.end) != (w.salary.start, w.salary.end) else "",
                     "transition": w.transition, "upcoming": upcoming,
                     "history": history.get(e["code"], [])})
    first_open = service.first_open_period(company)
    return Response({"period": str(period), "first_open": str(first_open) if first_open else "", "employees": rows})


@api_view(["POST"])
def assignments(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    d = request.data
    if d.get("clear"):
        n = service.clear_override(company, request.user, list(d.get("codes") or []), d.get("from", ""), d.get("note", ""))
    else:
        n = service.assign(company, request.user, list(d.get("codes") or []), d.get("group", ""), d.get("from", ""), d.get("note", ""))
    return Response({"changed": n}, status=201)
