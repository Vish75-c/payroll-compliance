"""Temporal activities: thin wrappers around the payroll services (which hold all the rules)."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from temporalio import activity
from temporalio.exceptions import ApplicationError

from payroll.shared.permissions import RuleViolation

from .. import runs as run_service
from ..events import record
from ..models import PayrollRun


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


ALL = [calculate, submit, approve, reject, cancel, finalize]
