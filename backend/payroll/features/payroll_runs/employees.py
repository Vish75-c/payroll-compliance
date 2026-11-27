"""Where each employee stands in a payroll month: waiting for HR data, ready, paid, or on hold.

The money is in the month's result; these rows only say who is in it. A person whose HR data lacks a fact pay
depends on (salary, joining date, work state, gender) waits and is left out of the calculation, so everyone else
can be paid. A maker can put a waiting person on hold, and at the cut-off the system holds everyone still waiting.
Someone on hold stops the next month from opening until their data arrives (``runs.previous_month_block``).

    waiting --data arrives--> ready --month final--> paid
       |  ^                     |
       |  +---data removed------+        (only while the month can still change)
       +--maker / cut-off--> on_hold --held salary paid in a later month--> paid_later
"""
from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone

from payroll_engine import policies as engine_policies

from payroll.shared.permissions import RuleViolation, require_role

from ..rules.policies import versions_for
from . import cycle
from .events import record
from .inputs import _in_period
from .models import PayrollRun, RunEmployee

FIELD_LABELS = RunEmployee.MISSING_FIELDS


def _labels(fields: list[str]) -> str:
    return ", ".join(FIELD_LABELS.get(f, f).lower() for f in fields)


def in_month(run: PayrollRun) -> list[dict]:
    """The employees of the run's HR data who work in its month (the same people the calculation considers)."""
    employees = run.snapshot.data.get("employees", [])
    windows = cycle.month_windows(run.company, run.period_obj, employees)
    return [e for e in employees if _in_period(e, windows[e["code"]].salary)]


def sync_states(run: PayrollRun, user=None) -> dict[str, int]:
    """Make the rows match the run's HR data: a person missing a fact waits, everyone else is ready. A person on hold
    whose data has arrived is ready again (the reason for the hold is gone). Only while the month can still change."""
    if run.run_type != PayrollRun.TYPE_REGULAR or run.is_locked:
        return counts(run)
    incomplete = run.snapshot.data.get("incomplete") or {}
    people = {e["code"]: e for e in in_month(run)}
    rows = {r.employee_code: r for r in run.employee_states.all()}
    for code, row in rows.items():
        if code not in people:
            row.delete()  # no longer in this month's HR data (left, or a code corrected)
    for code in sorted(people):
        missing = list(incomplete.get(code, []))
        row = rows.get(code)
        if row is None:
            RunEmployee.objects.create(run=run, employee_code=code, status=RunEmployee.WAITING if missing else RunEmployee.READY,
                                       missing=missing)
            continue
        if missing:
            new_status = RunEmployee.ON_HOLD if row.status == RunEmployee.ON_HOLD else RunEmployee.WAITING
        else:
            new_status = RunEmployee.READY
        if row.status == RunEmployee.ON_HOLD and new_status == RunEmployee.READY:
            record(run, user, "hold_released", f"{code}: the missing HR data ({_labels(row.missing)}) arrived, so {code} is "
                   "no longer on hold and is calculated with everyone else.", employee_code=code)
            row.held_at, row.held_by, row.hold_reason = None, None, ""
        if row.status != new_status or row.missing != missing:
            row.status, row.missing = new_status, missing
            row.save()
    return counts(run)


def counts(run: PayrollRun) -> dict[str, int]:
    out = {s: 0 for s, _ in RunEmployee.STATUS}
    for status in run.employee_states.values_list("status", flat=True):
        out[status] += 1
    return out


def waiting(run: PayrollRun) -> list[RunEmployee]:
    return list(run.employee_states.filter(status=RunEmployee.WAITING).order_by("employee_code"))


def _check_can_hold(run: PayrollRun, user):
    require_role(user, "maker", "put someone on hold")
    if run.run_type != PayrollRun.TYPE_REGULAR:
        raise RuleViolation("Only a regular month has people on hold.")
    if run.is_locked:
        raise RuleViolation(f"This month is {run.get_status_display().lower()}; nobody can be put on hold now.")


@transaction.atomic
def hold(run_id: int, code: str, user, reason: str) -> RunEmployee:
    """A maker puts a person who is waiting for HR data on hold: they are not paid this month, everyone else is."""
    run = PayrollRun.objects.select_for_update().get(pk=run_id)
    _check_can_hold(run, user)
    if not (reason or "").strip():
        raise RuleViolation("Say why this person is put on hold.")
    row = run.employee_states.filter(employee_code=code).first()
    if row is None:
        raise RuleViolation(f"{code} is not in {run.period_obj.label}.")
    if row.status == RunEmployee.ON_HOLD:
        return row
    if row.status != RunEmployee.WAITING:
        raise RuleViolation(f"Only someone waiting for HR data can be put on hold; {code} has complete data and is paid this month.")
    row.status, row.held_at, row.held_by, row.hold_reason = RunEmployee.ON_HOLD, timezone.now(), user, reason.strip()
    row.save()
    record(run, user, "held", f"{code} put on hold ({_labels(row.missing)} missing): {row.hold_reason} Not paid in "
           f"{run.period_obj.label}; the next month opens once the data arrives, and pays this month's salary.", employee_code=code)
    return row


@transaction.atomic
def hold_waiting_at_cut_off(run_id: int, today: date | None = None) -> list[RunEmployee]:
    """On or after the cut-off day, everyone still waiting is put on hold, so the month can go ahead for the others."""
    run = PayrollRun.objects.select_for_update().select_related("company").get(pk=run_id)
    if run.run_type != PayrollRun.TYPE_REGULAR or run.is_locked:
        return []
    today = today or timezone.localdate()
    cut_off = engine_policies.cut_off_date(versions_for(run.company), run.period_obj)
    if today < cut_off:
        return []
    held = []
    for row in waiting(run):
        row.status, row.held_at, row.held_by = RunEmployee.ON_HOLD, timezone.now(), None
        row.hold_reason = f"Still missing {_labels(row.missing)} at the cut-off ({cut_off:%d %b %Y})."
        row.save()
        held.append(row)
    if held:
        record(run, None, "held_at_cut_off", f"Cut-off {cut_off:%d %b %Y}: {len(held)} still waiting for HR data put on hold ("
               + ", ".join(r.employee_code for r in held) + "). Everyone else is paid this month.",
               employees=[r.employee_code for r in held])
    return held


def mark_paid(run: PayrollRun) -> int:
    """The month is final: everyone ready was paid in it."""
    return run.employee_states.filter(status=RunEmployee.READY).update(status=RunEmployee.PAID, updated_at=timezone.now())


def present(run: PayrollRun, user=None) -> dict:
    """Who is waiting or on hold, for the run page."""
    names = {e["code"]: e.get("name", e["code"]) for e in run.snapshot.data.get("employees", [])}
    rows = list(run.employee_states.select_related("held_by", "paid_in_run").order_by("employee_code"))
    cut_off = engine_policies.cut_off_date(versions_for(run.company), run.period_obj) if run.run_type == PayrollRun.TYPE_REGULAR else None
    from payroll.shared.permissions import is_in_group

    return {
        "counts": counts(run),
        "cut_off": cut_off.isoformat() if cut_off else None,
        "can_hold": bool(user) and is_in_group(user, "maker") and run.run_type == PayrollRun.TYPE_REGULAR and not run.is_locked,
        "people": [{
            "code": r.employee_code, "name": names.get(r.employee_code, r.employee_code), "status": r.status,
            "status_label": r.get_status_display(), "missing": r.missing, "missing_label": _labels(r.missing),
            "held_at": r.held_at.isoformat() if r.held_at else None, "held_by": r.held_by.get_username() if r.held_by else (
                "cut-off" if r.held_at else None),
            "hold_reason": r.hold_reason, "paid_in": r.paid_in_run.period_obj.label if r.paid_in_run else None,
        } for r in rows if r.status in (RunEmployee.WAITING, RunEmployee.ON_HOLD, RunEmployee.PAID_LATER)],
    }
