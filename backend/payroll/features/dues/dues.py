"""What the company must deposit with the government after a month is final, by when, and whether it has.

The amounts come from the final month's result and never change. Due dates come from the rule version
the month used (``due_day_next_month``), so a rule change also moves the date. A person records the
payment (date and challan / TRRN / CRN number); that, and any undo, goes into the month's audit trail.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from payroll_engine import policies as engine_policies
from payroll_engine.money import d, inr, to_str
from payroll_engine.periods import Period, parse_date

from ...shared.permissions import RuleViolation, is_in_group
from ..payroll_runs.events import record
from ..payroll_runs.models import PayrollRun
from .models import StatutoryDue

LABELS = dict(StatutoryDue.KINDS)
SOON_DAYS = 3


def due_date(period: Period, day: int | None) -> date | None:
    if not day:
        return None
    nxt = period.next()
    return date(nxt.year, nxt.month, min(int(day), nxt.days))


def _due_day(versions: list[dict], key: str, period: Period, jurisdiction: str) -> date | None:
    rule = engine_policies.resolve(versions, key, period, jurisdiction)
    if key == "income_tax" and period.month == 3:
        # TDS deducted in March is due by 30 April (Income-tax Rules, rule 30), not the 7th.
        return date(period.year, 4, 30)
    return due_date(period, rule and rule.payload.get("due_day_next_month"))


def amounts_of(result: dict) -> list[dict]:
    """The dues of one month, worked out from its result: one line per kind, and per state for PT."""
    sums: dict[tuple[str, str], list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0), 0])
    for e in result["employees"]:
        a = e["amounts"]
        for kind, emp_key, er_key, where in (
            (StatutoryDue.TDS, "tds", None, ""),
            (StatutoryDue.PF, "pf_employee", "pf_employer", ""),
            (StatutoryDue.ESI, "esi_employee", "esi_employer", ""),
            (StatutoryDue.PT, "pt", None, e.get("work_state", "")),
        ):
            emp_amt = d(a.get(emp_key, "0"))
            er_amt = d(a.get(er_key, "0")) if er_key else Decimal(0)
            line = sums[(kind, where)]
            line[0] += emp_amt
            line[1] += er_amt
            if emp_amt or er_amt:
                line[2] += 1
    order = [k for k, _ in StatutoryDue.KINDS]
    out = []
    for (kind, where), (emp_amt, er_amt, people) in sorted(sums.items(), key=lambda kv: (order.index(kv[0][0]), kv[0][1])):
        if emp_amt + er_amt == 0:
            continue  # e.g. no one is covered by ESI: nothing to deposit
        out.append({"kind": kind, "jurisdiction": where, "employee_amount": emp_amt, "employer_amount": er_amt,
                    "total": emp_amt + er_amt, "people": people})
    return out


def create_for_run(run: PayrollRun) -> list[StatutoryDue]:
    """Called when a month is finalised. Doing it twice changes nothing."""
    period = run.period_obj
    versions = (run.input or {}).get("policy_versions", [])
    keys = {StatutoryDue.TDS: ("income_tax", "IN"), StatutoryDue.PF: ("pf", "IN"), StatutoryDue.ESI: ("esi", "IN")}
    created = []
    for line in amounts_of(run.result):
        key, jurisdiction = keys.get(line["kind"], ("professional_tax", f"IN-{line['jurisdiction']}"))
        when = _due_day(versions, key, period, jurisdiction)
        if when is None:
            raise RuleViolation(f"The {LABELS[line['kind']]} rule used for {period.label} has no due day "
                                f"('due_day_next_month'), so its due date is unknown.")
        due, _ = StatutoryDue.objects.get_or_create(
            run=run, kind=line["kind"], jurisdiction=line["jurisdiction"],
            defaults={"company": run.company, "period": run.period, "due_date": when, "people": line["people"],
                      "employee_amount": line["employee_amount"], "employer_amount": line["employer_amount"], "total": line["total"]},
        )
        created.append(due)
    return created


def status_of(due: StatutoryDue, today: date | None = None) -> dict:
    today = today or timezone.localdate()
    if due.paid_on:
        late = (due.paid_on - due.due_date).days
        if late > 0:
            return {"code": "paid_late", "label": f"Paid {late} day{'s' if late != 1 else ''} late", "days": late}
        return {"code": "paid", "label": "Paid on time", "days": 0}
    left = (due.due_date - today).days
    if left < 0:
        return {"code": "overdue", "label": f"Overdue by {-left} day{'s' if left != -1 else ''}", "days": left}
    if left == 0:
        return {"code": "due_soon", "label": "Due today", "days": 0}
    if left <= SOON_DAYS:
        return {"code": "due_soon", "label": f"Due in {left} day{'s' if left != 1 else ''}", "days": left}
    return {"code": "due", "label": f"Due in {left} days", "days": left}


def title(due: StatutoryDue) -> str:
    return LABELS[due.kind] + (f" ({due.jurisdiction})" if due.jurisdiction else "")


def _require_payroll_role(user, what: str):
    """Government dues are the finance head's (checker role): only they see them and record payments."""
    if not is_in_group(user, "checker"):
        raise RuleViolation(f"Only the finance head can {what}.", code="forbidden")


@transaction.atomic
def mark_paid(due_id: int, user, paid_on: str, reference: str, note: str = "") -> StatutoryDue:
    _require_payroll_role(user, "record a payment to the government")
    due = StatutoryDue.objects.select_for_update().select_related("run").get(pk=due_id)
    if due.paid_on:
        raise RuleViolation(f"{title(due)} for {Period.parse(due.period).label} is already recorded as paid on "
                            f"{due.paid_on:%d %b %Y}. Undo that first to change it.")
    try:
        when = parse_date(paid_on)
    except ValueError:
        when = None
    if when is None:
        raise RuleViolation("Give the date the money was paid.")
    period = Period.parse(due.period)
    if when > timezone.localdate():
        raise RuleViolation("The payment date cannot be in the future.")
    if when < period.start:
        raise RuleViolation(f"The payment date is before {period.label} began.")
    reference = (reference or "").strip()
    if not reference:
        raise RuleViolation("Give the challan, TRRN or CRN number of the payment, so it can be traced.")
    due.paid_on, due.reference, due.note = when, reference[:60], (note or "").strip()
    due.paid_by, due.paid_at = user, timezone.now()
    due.save()
    state = status_of(due)
    record(due.run, user, "due_paid", f"{title(due)} of {inr(due.total)} recorded as paid on {when:%d %b %Y} "
                                      f"(ref. {due.reference}). {state['label']}.",
           due_id=due.pk, kind=due.kind, jurisdiction=due.jurisdiction, total=to_str(due.total),
           paid_on=when.isoformat(), reference=due.reference)
    return due


@transaction.atomic
def undo_paid(due_id: int, user, reason: str) -> StatutoryDue:
    _require_payroll_role(user, "undo a recorded payment")
    due = StatutoryDue.objects.select_for_update().select_related("run").get(pk=due_id)
    if not due.paid_on:
        raise RuleViolation("This due is not recorded as paid.")
    if not (reason or "").strip():
        raise RuleViolation("Say why the payment record is being undone.")
    was = f"paid on {due.paid_on:%d %b %Y}, ref. {due.reference}"
    due.paid_on, due.reference, due.paid_by, due.paid_at, due.note = None, "", None, None, ""
    due.save()
    record(due.run, user, "due_paid_undone", f"{title(due)} payment record undone (was {was}): {reason.strip()}",
           due_id=due.pk, kind=due.kind, jurisdiction=due.jurisdiction)
    return due
