"""Plain-English changes: the AI proposes, plain code checks, a person confirms, the engine calculates.

What each kind of change checks and does is in ``change_kinds``; this module handles what all changes share:
which employee, which month, proposing, confirming, discarding, and the audit trail."""
from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone

from payroll_engine.money import to_str
from payroll_engine.periods import Period

from payroll.shared.permissions import RuleViolation, require_role

from ..ai import tasks as ai_tasks
from ..hr_data.values import ValueError_, parse_amount, parse_number
from .change_kinds import Form, Problem, Sentence, date_written, kind_of  # noqa: F401  (date_written: used by tests)
from .events import record
from .models import ChangeRequest, PayrollRun


def _employees(run: PayrollRun) -> list[dict]:
    return [{"code": e["code"], "name": e.get("name", e["code"])} for e in run.snapshot.data["employees"]]


def resolve_employee(written: str, employees: list[dict]) -> tuple[dict | None, str]:
    w = re.sub(r"[’']s\b", "", (written or "").strip().lower())
    w = re.sub(r"\s+", " ", w)
    if not w:
        return None, "The instruction does not say which employee."
    for e in employees:
        if e["code"].lower() == w or e["name"].lower() == w:
            return e, ""
    tokens = [t for t in re.split(r"[^a-z0-9]+", w) if t]
    matches = [e for e in employees if tokens and all(t in re.split(r"[^a-z0-9]+", e["name"].lower()) for t in tokens)]
    if len(matches) == 1:
        return matches[0], ""
    if len(matches) > 1:
        return None, f"'{written}' could be " + " or ".join(f"{m['name']} ({m['code']})" for m in matches) + ". Please use the full name or ID."
    return None, f"No employee called '{written}' in this month's data."


def _amount_values_in(text: str) -> set[str]:
    values = set()
    for written in ai_tasks.amounts_written(text):
        try:
            values.add(to_str(parse_amount(written)))
        except ValueError_:
            continue
    return values


def validate_proposal(raw: dict, text: str, run: PayrollRun) -> dict:
    """Check every proposed change with plain code. Problems block confirmation."""
    employees = _employees(run)
    period = run.period_obj
    items, problems = [], []
    written_amounts = _amount_values_in(text)
    numbers_in_text = {to_str(parse_number(n)) for n in re.findall(r"\b\d+(?:\.\d+)?\b", text)}
    for change in raw.get("changes", []):
        kind = change.get("type")
        emp, why = resolve_employee(change.get("employee_as_written", ""), employees)
        if not emp:
            problems.append(why)
            continue
        month = change.get("month")
        if month:
            try:
                target = Period.parse(month)
            except ValueError:
                problems.append(f"Could not read the month '{month}'.")
                continue
            if target != period:
                problems.append(f"This change is for {target.label}, but you are preparing {period.label}. Open {target.label} to add it.")
                continue
        item = {"type": kind, "employee_code": emp["code"], "employee_name": emp["name"]}
        sentence = Sentence(run=run, period=period, emp=emp, text=text, change=change, written_amounts=written_amounts,
                            numbers_in_text=numbers_in_text)
        try:
            kind_of(kind).from_sentence(sentence, item)
        except Problem as problem:
            problems.append(str(problem))
            continue
        items.append(item)
    notes = []
    if not items and not problems:
        problems.append(raw.get("unclear") or "Could not find a change in this instruction.")
    elif raw.get("unclear"):
        # An AI remark next to a change that passed every check is shown to the maker, not a blocker.
        (notes if items else problems).append(raw["unclear"])
    return {"items": items, "problems": problems, "notes": notes}


def _check_month_open(run: PayrollRun):
    regular = PayrollRun.objects.filter(company=run.company, period=run.period, run_type=PayrollRun.TYPE_REGULAR).exclude(
        status=PayrollRun.CANCELLED).first()
    if regular and regular.is_locked:
        raise RuleViolation(f"{run.period_obj.label} is {regular.get_status_display().lower()}; changes are closed.")


@transaction.atomic
def propose(run: PayrollRun, text: str, user, force: bool = False) -> ChangeRequest:
    require_role(user, "maker", "enter changes")
    text = (text or "").strip()
    if not text:
        raise RuleViolation("Type an instruction first.")
    if len(text) > 1000:
        raise RuleViolation("Please keep an instruction under 1000 characters.")
    _check_month_open(run)
    interaction = ai_tasks.parse_instruction(company=run.company, text=text, period=run.period, employees=_employees(run), force=force)
    checked = validate_proposal(interaction.response or {}, text, run)
    checked["ai"] = {"provider": interaction.provider, "model": interaction.model, "interaction_id": interaction.pk,
                     "reused_saved_answer": getattr(interaction, "reused", False), "raw": interaction.response}
    change = ChangeRequest.objects.create(company=run.company, period=run.period, run=run, text=text, ai=interaction,
                                          proposal=checked, created_by=user)
    record(run, user, "change_proposed", f'Instruction entered: "{text}"', change_id=change.pk, problems=checked["problems"])
    return change


@transaction.atomic
def confirm(change: ChangeRequest, user) -> ChangeRequest:
    require_role(user, "maker", "confirm changes")
    change = ChangeRequest.objects.select_for_update().get(pk=change.pk)
    if change.status != ChangeRequest.PROPOSED:
        raise RuleViolation(f"This change is already {change.status}.")
    if change.proposal.get("problems"):
        raise RuleViolation("This change has problems and cannot be confirmed. Reword the instruction.")
    if not change.proposal.get("items"):
        raise RuleViolation("Nothing to confirm.")
    run = change.run
    _check_month_open(run)
    change.status = ChangeRequest.CONFIRMED
    change.decided_by = user
    change.decided_at = timezone.now()
    change.save()
    if change.ai_id:
        change.ai.status = "accepted"
        change.ai.reviewed_by = user
        change.ai.reviewed_at = timezone.now()
        change.ai.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    if change.proposal.get("source") == "monthly_inputs":
        from . import monthly_inputs

        monthly_inputs.remember(change, user)
    _invalidate_open_runs(run, user, "a change was confirmed")
    record(run, user, "change_confirmed", "Confirmed: " + " ".join(i["summary"] for i in change.proposal["items"]), change_id=change.pk)
    return change


@transaction.atomic
def reject(change: ChangeRequest, user) -> ChangeRequest:
    require_role(user, "maker", "discard changes")
    change = ChangeRequest.objects.select_for_update().get(pk=change.pk)
    if change.status == ChangeRequest.CONFIRMED:
        _check_month_open(change.run)
        _invalidate_open_runs(change.run, user, "a confirmed change was removed")
    change.status = ChangeRequest.REJECTED
    change.decided_by = user
    change.decided_at = timezone.now()
    change.save()
    if change.ai_id and change.ai.status == "pending_review":
        change.ai.status = "rejected"
        change.ai.reviewed_by = user
        change.ai.reviewed_at = timezone.now()
        change.ai.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    record(change.run, user, "change_discarded", f'Discarded: "{change.text}"', change_id=change.pk)
    return change


# --------------------------------------------------------------------------- entered by hand (no AI)

@transaction.atomic
def add_by_hand(run: PayrollRun, user, data: dict) -> ChangeRequest:
    """A change HR enters in a form: an unpaid absence (e.g. absent without approval, which no HR tool shows as
    leave) in this month or in a month already paid (recovered now), a salary change (arrears follow for months
    already paid), a one-time payment or a deduction. A person typed every value, so it is confirmed as entered; it
    is in the audit trail with who and why, and the checker still approves the month."""
    require_role(user, "maker", "enter changes")
    _check_month_open(run)
    period = run.period_obj
    kind = data.get("kind")
    emp = next((e for e in _employees(run) if e["code"] == data.get("employee_code")), None)
    if not emp:
        raise RuleViolation("Pick an employee from this month's HR data.")
    reason = " ".join(str(data.get("reason") or "").split())[:300]
    item = {"type": kind, "employee_code": emp["code"], "employee_name": emp["name"], "entered_by_hand": True}
    text = kind_of(kind).from_form(Form(run=run, period=period, emp=emp, data=data, reason=reason), item)
    change = ChangeRequest.objects.create(
        company=run.company, period=run.period, run=run, text=text, created_by=user, status=ChangeRequest.CONFIRMED,
        decided_by=user, decided_at=timezone.now(), proposal={"items": [item], "problems": [], "notes": [], "entered_by_hand": True},
    )
    _invalidate_open_runs(run, user, "a change was added")
    record(run, user, "change_added_by_hand", f"Added by hand: {item['summary']}", change_id=change.pk)
    return change


def _invalidate_open_runs(run: PayrollRun, user, why: str):
    for other in PayrollRun.objects.filter(company=run.company, period=run.period,
                                           status__in=[PayrollRun.CALCULATED, PayrollRun.REJECTED]):
        other.status = PayrollRun.DRAFT
        other.save(update_fields=["status", "updated_at"])
        record(other, user, "needs_recalculation", f"Needs to be calculated again because {why}.")

