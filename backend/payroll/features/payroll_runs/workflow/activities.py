"""Temporal activities: thin wrappers around the payroll services (which hold all the rules)."""
from __future__ import annotations

from datetime import datetime, time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.utils import timezone
from temporalio import activity
from temporalio.exceptions import ApplicationError

from payroll_engine import policies as engine_policies

from payroll.shared.permissions import RuleViolation

from ...rules.policies import versions_for
from .. import employees as employee_service
from .. import runs as run_service
from ..events import record
from ..models import PayrollRun, RunEmployee
from .definitions import HRSyncWorkflow, SyncRequest, Timing


def _call(name, fn, run_id, actor_id, *args):
    close_old_connections()
    user = get_user_model().objects.filter(pk=actor_id).first() if actor_id else None
    try:
        run = fn(run_id, user, *args)
        return run.status
    except RuleViolation as exc:
        run = PayrollRun.objects.filter(pk=run_id).first()
        if run:
            run.last_error = exc.message
            run.save(update_fields=["last_error", "updated_at"])
            record(run, user, f"{name}_refused", f"Could not {name}: {exc.message}")
        raise ApplicationError(exc.message, type="RuleViolation", non_retryable=True) from exc
    finally:
        close_old_connections()


@activity.defn(name="payroll.calculate")
def calculate(run_id: int, actor_id: int, note: str) -> str:
    return _call("calculate", run_service.calculate, run_id, actor_id)


@activity.defn(name="payroll.submit")
def submit(run_id: int, actor_id: int, note: str) -> str:
    return _call("submit", run_service.submit, run_id, actor_id, note)


@activity.defn(name="payroll.approve")
def approve(run_id: int, actor_id: int, note: str) -> str:
    return _call("approve", run_service.approve, run_id, actor_id, note)


@activity.defn(name="payroll.reject")
def reject(run_id: int, actor_id: int, note: str) -> str:
    return _call("send back", run_service.reject, run_id, actor_id, note)


@activity.defn(name="payroll.cancel")
def cancel(run_id: int, actor_id: int, note: str) -> str:
    return _call("cancel", run_service.cancel, run_id, actor_id, note)


@activity.defn(name="payroll.finalize")
def finalize(run_id: int, actor_id: int, note: str) -> str:
    return _call("finalise", lambda rid, user: run_service.finalize(rid, user), run_id, actor_id)


@activity.defn(name="payroll.cut_off")
def cut_off(run_id: int, actor_id: int, note: str) -> str:
    """The cut-off day has come: everyone still waiting for HR data is put on hold."""
    close_old_connections()
    try:
        employee_service.hold_waiting_at_cut_off(run_id)
        return PayrollRun.objects.get(pk=run_id).status
    finally:
        close_old_connections()


# --------------------------------------------------------------------------- the people of a month

@activity.defn(name="payroll.month_people")
def month_people(run_id: int) -> dict:
    """Who is in the month (one workflow each) and when its cut-off is."""
    close_old_connections()
    try:
        run = PayrollRun.objects.select_related("company").get(pk=run_id)
        employee_service.sync_states(run)
        day = engine_policies.cut_off_date(versions_for(run.company), run.period_obj)
        at = timezone.make_aware(datetime.combine(day, time.min))  # the start of the cut-off day, in the company's time
        return {"company_code": run.company.code, "codes": list(run.employee_states.values_list("employee_code", flat=True)),
                "cut_off_at": at.timestamp()}
    finally:
        close_old_connections()


@activity.defn(name="payroll.employee_state")
def employee_state(run_id: int, code: str) -> dict:
    close_old_connections()
    try:
        row = RunEmployee.objects.filter(run_id=run_id, employee_code=code).first()
        if row is None:
            return {"status": "gone", "missing": []}  # no longer in the month's HR data
        return {"status": row.status, "missing": row.missing}
    finally:
        close_old_connections()


@activity.defn(name="payroll.remind_hr")
def remind_hr(run_id: int, code: str, missing: list[str]) -> None:
    close_old_connections()
    try:
        run = PayrollRun.objects.get(pk=run_id)
        if not run.employee_states.filter(employee_code=code, status=RunEmployee.WAITING).exists():
            return
        record(run, None, "hr_reminder", f"Reminder for HR: {code} still has no {employee_service._labels(missing)}. Until it is "
               f"added, {code} is not paid in {run.period_obj.label}, and is put on hold at the cut-off.", employee_code=code)
    finally:
        close_old_connections()


@activity.defn(name="hrsync.request")
async def request_hr_data(company_code: str, req: SyncRequest, timing: Timing) -> None:
    """Ask the company's HRSync for one person's missing data. Starts it if it is not running (signal-with-start), so
    however many people ask at once there is one HRSync, and one read for all of them."""
    await activity.client().start_workflow(
        HRSyncWorkflow.run, args=[company_code, timing], id=hr_sync_id(company_code), task_queue=settings.TEMPORAL_TASK_QUEUE,
        start_signal="need", start_signal_args=[req])


@activity.defn(name="hrsync.read")
def read_hr_tool(company_code: str, requests: list[SyncRequest]) -> dict:
    """The agent reads the HR tool once for everyone in ``requests`` (runs on the agent's own task queue)."""
    from ...hr_data import sync

    try:
        return sync.read_for_waiting(company_code, [{"run_id": r.run_id, "code": r.code, "missing": list(r.missing)} for r in requests])
    finally:
        close_old_connections()


AGENT = [read_hr_tool]


def hr_sync_id(company_code: str) -> str:
    return f"hr-sync-{company_code}"


ALL = [calculate, submit, approve, reject, cancel, finalize, cut_off, month_people, employee_state, remind_hr, request_hr_data]

