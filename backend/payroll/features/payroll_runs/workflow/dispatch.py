"""Send a person's action to the month's workflow (Temporal), or run it in-process (local mode).

Either way the same service functions run and the same checks apply. The API checks the
request first so that a person gets an immediate, clear answer.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from django.conf import settings
from django.db import transaction

from payroll.shared.permissions import RuleViolation, require_role

from .. import runs as run_service
from ..events import record
from ..models import PayrollRun
from .definitions import Command, PayrollMonthWorkflow

log = logging.getLogger("payroll.workflow")

def precheck(command: str, run: PayrollRun, user, note: str):
    """The same checks the activity will make, run now so the person gets an immediate answer."""
    if command == "calculate":
        run_service.check_calculate(run, user)
    elif command == "submit":
        run_service.check_submit(run, user)
    elif command in ("approve", "reject"):
        run_service.check_decide(run, user)
        if command == "reject" and not (note or "").strip():
            raise RuleViolation("Say why the month is being sent back.")
    elif command == "cancel":
        require_role(user, "maker", "cancel a month")
    elif command == "finalize":
        run_service.check_finalize(run, user)
    else:
        raise RuleViolation(f"Unknown action '{command}'.")


LOCAL = {
    "calculate": lambda run_id, user, note: run_service.calculate(run_id, user),
    "submit": lambda run_id, user, note: run_service.submit(run_id, user, note),
    "approve": lambda run_id, user, note: run_service.finalize(run_service.approve(run_id, user, note).pk, user),
    "reject": lambda run_id, user, note: run_service.reject(run_id, user, note),
    "cancel": lambda run_id, user, note: run_service.cancel(run_id, user, note),
    "finalize": lambda run_id, user, note: run_service.finalize(run_id, user),
}


def workflow_id(run: PayrollRun) -> str:
    return f"payroll-{run.company.code}-{run.period}-run{run.pk}"


async def _client():
    from temporalio.client import Client

    return await Client.connect(settings.TEMPORAL_ADDRESS, namespace=settings.TEMPORAL_NAMESPACE)


def _temporal(coro_factory):
    try:
        return asyncio.run(coro_factory())
    except RuleViolation:
        raise
    except Exception as exc:  # noqa: BLE001 - any connection problem becomes a clear message
        log.warning("Temporal call failed: %s", exc)
        raise RuleViolation(
            f"The workflow engine (Temporal at {settings.TEMPORAL_ADDRESS}) is not reachable: {exc}. "
            "Start it with ./scripts/start.sh, or set WORKFLOW_BACKEND=local.", code="workflow_unavailable"
        ) from exc


def start(run: PayrollRun) -> PayrollRun:
    """Start the workflow for a regular month. Drafts are what-ifs and need no workflow."""
    if run.run_type != PayrollRun.TYPE_REGULAR:
        run.workflow_backend = "none"
        run.save(update_fields=["workflow_backend"])
        return run
    if settings.WORKFLOW_BACKEND != "temporal":
        run.workflow_backend = "local"
        run.save(update_fields=["workflow_backend"])
        return run
    wid = workflow_id(run)

    async def go():
        from temporalio.exceptions import WorkflowAlreadyStartedError

        client = await _client()
        try:
            await client.start_workflow(PayrollMonthWorkflow.run, run.pk, id=wid, task_queue=settings.TEMPORAL_TASK_QUEUE)
        except WorkflowAlreadyStartedError:
            pass  # started by an earlier attempt whose reply was lost: the same month, keep it

    _temporal(go)
    run.workflow_backend = "temporal"
    run.workflow_id = wid
    run.save(update_fields=["workflow_backend", "workflow_id"])
    record(run, None, "workflow_started", f"Workflow {wid} started in Temporal; it waits for each step without holding a process.")
    return run


def needs_workflow(run: PayrollRun) -> bool:
    """A regular month that should be in Temporal but has no workflow yet (Temporal was down when it was opened)."""
    return (run.run_type == PayrollRun.TYPE_REGULAR and settings.WORKFLOW_BACKEND == "temporal"
            and not run.workflow_backend and run.status not in (PayrollRun.FINALIZED, PayrollRun.CANCELLED))


def send(run: PayrollRun, command: str, user, note: str = "") -> dict:
    precheck(command, run, user, note)
    if needs_workflow(run):
        # Never fall back to running in-process: that month would not wait durably for its checker.
        start(run)
    if run.workflow_backend == "temporal" and run.workflow_id:
        wid = run.workflow_id

        async def go():
            client = await _client()
            handle = client.get_workflow_handle(wid)
            await handle.signal(PayrollMonthWorkflow.command,
                                Command(name=command, actor_id=user.pk, note=note, request_id=uuid.uuid4().hex))

        _temporal(go)
        record(run, user, f"{command}_requested", f"Asked the workflow to {command.replace('_', ' ')}.")
        return {"mode": "temporal", "queued": True}
    with transaction.atomic():
        LOCAL[command](run.pk, user, note)
    return {"mode": "local", "queued": False}


def describe(run: PayrollRun) -> dict:
    info = {"backend": run.workflow_backend or "local", "workflow_id": run.workflow_id, "temporal_ui": ""}
    if run.workflow_backend == "temporal" and run.workflow_id:
        info["temporal_ui"] = f"{settings.TEMPORAL_UI_URL}/namespaces/{settings.TEMPORAL_NAMESPACE}/workflows/{run.workflow_id}"

        async def go():
            client = await _client()
            handle = client.get_workflow_handle(run.workflow_id)
            desc = await handle.describe()
            state = None
            if desc.status and desc.status.name == "RUNNING":
                state = await handle.query(PayrollMonthWorkflow.state)
            return {"status": desc.status.name if desc.status else "UNKNOWN", "state": state}

        try:
            info.update(asyncio.run(go()))
            info["reachable"] = True
        except Exception as exc:  # noqa: BLE001
            info["reachable"] = False
            info["error"] = str(exc)
    return info
