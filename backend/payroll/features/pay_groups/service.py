"""Pay groups: which people are paid on which cycle, and changing that without paying a day twice.

A person's group for a month, in this order: a row set by hand (``manual``), else the row the HR data last gave
(a "Pay group" column, or the first group whose rule covers their department, work state or designation), else the
default group. Rows are dated by month, and nothing can be changed for a month that is already final.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.utils.text import slugify

from payroll_engine import policies as engine_policies
from payroll_engine.periods import Period

from ...shared.permissions import RuleViolation, require_role
from ..companies.models import Company
from ..hr_data.models import DataSnapshot
from ..payroll_runs.models import PayrollRun
from .models import FROM_THE_START, EmployeePayGroup, PayGroup, PayGroupCycle

COVERS = (("departments", "department", "Department"), ("work_states", "work_state", "Work state"),
          ("designations", "designation", "Designation"))


@dataclass
class GroupNow:
    """A group as it is in one month."""
    group: PayGroup
    cycle: PayGroupCycle
    pay_day: int | None

    @property
    def payload(self) -> dict:
        return {"mode": self.cycle.mode, "start_day": self.cycle.start_day}


# --------------------------------------------------------------------------- reading

def last_final_period(company: Company) -> Period | None:
    last = (PayrollRun.objects.filter(company=company, run_type=PayrollRun.TYPE_REGULAR, status=PayrollRun.FINALIZED)
            .order_by("-period").values_list("period", flat=True).first())
    return Period.parse(last) if last else None


def first_open_period(company: Company) -> Period | None:
    last = last_final_period(company)
    return last.next() if last else None


def first_open_period_or_today(company: Company) -> Period:
    """The month the screens show by default: the first one not final, or this month."""
    return first_open_period(company) or Period.of(date.today())


def ensure_default(company: Company) -> PayGroup:
    group, created = PayGroup.objects.get_or_create(company=company, code="default",
                                                    defaults={"name": "Everyone else", "is_default": True})
    if created or not group.cycles.exists():
        PayGroupCycle.objects.get_or_create(group=group, effective_from=FROM_THE_START, defaults={"mode": "calendar", "start_day": 1})
    return group


def groups_for(company: Company, period: Period) -> list[GroupNow]:
    """Every group that has a cycle in ``period``, the default group first."""
    ensure_default(company)
    out = []
    for group in PayGroup.objects.filter(company=company).prefetch_related("cycles"):
        cycles = [c for c in group.cycles.all() if c.effective_from <= period.start]
        if cycles:
            cycle = cycles[-1]
            out.append(GroupNow(group, cycle, cycle.pay_day or (company.pay_day if group.is_default else None)))
    return out


def rows_as_of(company: Company, period: Period | None = None) -> dict[str, EmployeePayGroup]:
    """Each person's row in force in ``period`` (or their latest row, with no period)."""
    qs = EmployeePayGroup.objects.filter(company=company).select_related("group")
    if period is not None:
        qs = qs.filter(effective_from__lte=period.start)
    out = {}
    for row in qs.order_by("effective_from", "id"):
        out[row.employee_code] = row
    return out


def _same(a, b) -> bool:
    return str(a or "").strip().lower() == str(b or "").strip().lower()


def derive(emp: dict, groups: list[GroupNow]) -> tuple[GroupNow, str, str]:
    """The group the HR data puts a person in: (group, source, reason)."""
    default = next(g for g in groups if g.group.is_default)
    given = emp.get("pay_group")
    if given:
        match = next((g for g in groups if _same(g.group.name, given) or _same(g.group.code, given)), None)
        if match:
            return match, EmployeePayGroup.HR_DATA, f"Pay group column: {given}"
    for g in groups:
        if g.group.is_default:
            continue
        for key, field, label in COVERS:
            if any(_same(emp.get(field), value) for value in (g.cycle.covers or {}).get(key, [])):
                return g, EmployeePayGroup.RULE, f"{label}: {emp.get(field)}"
    return default, EmployeePayGroup.DEFAULT, ""


def group_of(emp: dict, groups: list[GroupNow], rows: dict[str, EmployeePayGroup]) -> tuple[GroupNow, str, str]:
    """A person's group in a month: their dated row if its group is there that month, else what the data says."""
    row = rows.get(emp["code"])
    if row:
        match = next((g for g in groups if g.group.pk == row.group_id), None)
        if match:
            return match, row.source, row.reason
    return derive(emp, groups)


def unknown_group_values(employees: list[dict], groups: list[GroupNow]) -> list[str]:
    """"Pay group" values in the HR data that name no group (they fall back to the group rules)."""
    names = {n for g in groups for n in (g.group.name.lower(), g.group.code.lower())}
    return sorted({e["pay_group"] for e in employees if e.get("pay_group") and e["pay_group"].strip().lower() not in names})


# --------------------------------------------------------------------------- changing

def _check_open(company: Company, period: Period, what: str):
    last = last_final_period(company)
    if last and period <= last:
        raise RuleViolation(f"{last.label} is final, so {what} can only start from {last.next().label} or later.",
                            code="month_final")


def _start(company: Company, month: str | None) -> date:
    """The 1st of ``month``; with no month, the first month that is not final (or from the start)."""
    if month:
        try:
            p = Period.parse(month)
        except ValueError as exc:
            raise RuleViolation(str(exc)) from exc
        _check_open(company, p, "this change")
        return p.start
    first = first_open_period(company)
    return first.start if first else FROM_THE_START


def _clean_cycle(data: dict) -> dict:
    mode = data.get("mode") or "calendar"
    payload = {"mode": mode, "start_day": 1 if mode == "calendar" else data.get("start_day")}
    errors = engine_policies.validate_payload("pay_cycle", payload)
    pay_day = data.get("pay_day")
    if pay_day in ("", None):
        pay_day = None
    else:
        try:
            pay_day = int(pay_day)
        except (TypeError, ValueError):
            pay_day = 0
        if not 1 <= pay_day <= 31:
            errors.append("the pay day must be a day of the month, from 1 to 31")
    covers = {}
    for key, _, _ in COVERS:
        values = (data.get("covers") or {}).get(key) or []
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            errors.append(f"'{key}' must be a list of names")
            continue
        covers[key] = sorted({v.strip() for v in values if v.strip()}, key=str.lower)
    if errors:
        raise RuleViolation("The pay group is not valid: " + "; ".join(errors), code="invalid_payload")
    return {"mode": mode, "start_day": int(payload["start_day"]), "pay_day": pay_day, "covers": covers}


@transaction.atomic
def create_group(company: Company, user, data: dict) -> PayGroup:
    require_role(user, "maker", "add a pay group")
    ensure_default(company)
    name = " ".join(str(data.get("name") or "").split())[:100]
    if not name:
        raise RuleViolation("Give the pay group a name, e.g. Factory staff.")
    if PayGroup.objects.filter(company=company, name__iexact=name).exists():
        raise RuleViolation(f"There is already a pay group called {name}.", code="exists")
    cycle = _clean_cycle(data)
    start = _start(company, data.get("from"))
    base = slugify(name)[:36] or "group"
    code, n = base, 2
    while PayGroup.objects.filter(company=company, code=code).exists():
        code, n = f"{base}-{n}", n + 1
    group = PayGroup.objects.create(company=company, code=code, name=name)
    PayGroupCycle.objects.create(group=group, effective_from=start, note=str(data.get("note") or "")[:300], created_by=user, **cycle)
    refresh_from_latest_data(company)
    return group


@transaction.atomic
def change_cycle(group: PayGroup, user, data: dict) -> PayGroupCycle:
    """A new cycle, pay day or rule for a group from a month on. The first month on a new cycle is a transition
    period, so no day is paid twice or skipped."""
    require_role(user, "maker", "change a pay group")
    cycle = _clean_cycle(data)
    if group.is_default:
        cycle["covers"] = {}  # the default group takes everyone the others do not
        cycle["pay_day"] = None  # its pay day is the company's "salary paid on" day
    start = _start(group.company, data.get("from"))
    row, _ = PayGroupCycle.objects.update_or_create(
        group=group, effective_from=start,
        defaults={**cycle, "note": str(data.get("note") or "")[:300], "created_by": user})
    refresh_from_latest_data(group.company)
    return row


@transaction.atomic
def assign(company: Company, user, codes: list[str], group_code: str, month: str, note: str = "") -> int:
    """Set people's group by hand from ``month``. The HR data never replaces it; clear it to go back to the data."""
    require_role(user, "maker", "move people between pay groups")
    if not codes:
        raise RuleViolation("Pick the people to move.")
    if not month:
        raise RuleViolation("Pick the month the move starts from.")
    start = _start(company, month)
    group = PayGroup.objects.filter(company=company, code=group_code).first()
    if group is None or not group.cycles.filter(effective_from__lte=start).exists():
        raise RuleViolation("Pick a pay group that exists in that month.")
    note = " ".join(str(note or "").split())[:300]
    for code in codes:
        EmployeePayGroup.objects.update_or_create(
            company=company, employee_code=code, effective_from=start, source=EmployeePayGroup.MANUAL,
            defaults={"group": group, "reason": "Set by hand", "note": note, "created_by": user})
    return len(codes)


@transaction.atomic
def clear_override(company: Company, user, codes: list[str], month: str, note: str = "") -> int:
    """Go back to what the HR data says, from ``month``."""
    require_role(user, "maker", "move people between pay groups")
    start = _start(company, month)
    period = Period.of(start)
    groups = groups_for(company, period)
    employees = {e["code"]: e for e in latest_employees(company)}
    for code in codes:
        g, source, reason = derive(employees.get(code, {"code": code}), groups)
        EmployeePayGroup.objects.filter(company=company, employee_code=code, effective_from=start, source=EmployeePayGroup.MANUAL).delete()
        EmployeePayGroup.objects.create(company=company, employee_code=code, group=g.group, effective_from=start, source=source,
                                        reason=reason, note=" ".join(str(note or "Hand setting cleared").split())[:300], created_by=user)
    return len(codes)


def latest_employees(company: Company) -> list[dict]:
    snap = DataSnapshot.objects.filter(company=company, status="approved").order_by("-reviewed_at", "-id").first()
    return snap.data.get("employees", []) if snap else []


def refresh_from_latest_data(company: Company) -> int:
    snap = DataSnapshot.objects.filter(company=company, status="approved").order_by("-reviewed_at", "-id").first()
    return refresh_from_data(company, snap.data.get("employees", [])) if snap else 0


def refresh_from_data(company: Company, employees: list[dict]) -> int:
    """After reviewed HR data (or a group change): add a row for each person whose group the data now gives differs
    from their last row, from the first month that is not final. Rows set by hand are left alone."""
    first = first_open_period(company)
    start = first.start if first else FROM_THE_START
    groups = groups_for(company, first or Period.of(date.today()))
    latest = rows_as_of(company)
    added = 0
    for emp in employees:
        last = latest.get(emp["code"])
        if last and last.source == EmployeePayGroup.MANUAL:
            continue
        g, source, reason = derive(emp, groups)
        if last and last.group_id == g.group.pk:
            if (last.source, last.reason) != (source, reason) and last.effective_from >= start:
                last.source, last.reason = source, reason
                last.save(update_fields=["source", "reason"])
            continue
        if last and last.effective_from >= start:
            last.group, last.source, last.reason = g.group, source, reason
            last.save(update_fields=["group", "source", "reason"])
        else:
            EmployeePayGroup.objects.create(company=company, employee_code=emp["code"], group=g.group, effective_from=start,
                                            source=source, reason=reason)
        added += 1
    return added
