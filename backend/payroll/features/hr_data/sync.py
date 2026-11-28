"""Read the HR tool again for the people waiting for HR data: one read for all of them.

The agent reads the whole of the HR tool's confirmed screens (as the "Read HR tool" button does), so one read covers
everyone waiting, in every open month. The new HR data then waits for the finance team employee's review, like any
other read: nothing is used for pay until a person has looked at it and chosen it for the month.
"""
from __future__ import annotations

import logging

from django.db import close_old_connections

from ..payroll_runs.events import record
from ..payroll_runs.models import PayrollRun, RunEmployee
from . import jobs, snapshots
from .models import Connector, IngestionJob

log = logging.getLogger("payroll.ingestion")
LABELS = RunEmployee.MISSING_FIELDS


def _labels(fields) -> str:
    return ", ".join(LABELS.get(f, f).lower() for f in fields)


def read_for_waiting(company_code: str, requests: list[dict]) -> dict:
    """``requests``: [{"run_id", "code", "missing"}]. Returns the read's outcome, who was found, and who is still missing.

    ``stop`` says that reading again will not help until a person acts (no HR tool set up, a new column to map, or
    data with errors)."""
    close_old_connections()
    key = lambda r: f"{r['run_id']}:{r['code']}"  # noqa: E731
    wanted = [r for r in requests if RunEmployee.objects.filter(run_id=r["run_id"], employee_code=r["code"],
                                                                status=RunEmployee.WAITING).exists()]
    if not wanted:
        return {"status": "nobody_waiting", "found": [], "still_missing": [], "stop": False}
    runs = {r.pk: r for r in PayrollRun.objects.filter(pk__in={r["run_id"] for r in wanted}).select_related("company", "created_by")}
    company = next(iter(runs.values())).company
    who = "; ".join(f"{r['code']} ({_labels(r['missing'])})" for r in wanted)

    def tell(action: str, message: str, **detail):
        for run in runs.values():
            record(run, None, action, message, people=[r["code"] for r in wanted if r["run_id"] == run.pk], **detail)

    connector = (Connector.objects.filter(company=company, kind=Connector.KIND_BROWSER, recipe_confirmed_at__isnull=False)
                 .order_by("-id").first())
    if connector is None:
        tell("hr_sync_no_tool", f"Waiting for HR data: {who}. There is no HR tool set up to read automatically, so upload a "
             "spreadsheet with the missing details on the HR data page.")
        return {"status": "no_hr_tool", "found": [], "still_missing": [key(r) for r in wanted], "stop": True}

    user = next((r.created_by for r in runs.values() if r.created_by), None)
    job = IngestionJob.objects.create(company=company, connector=connector, kind="browser", source_system=connector.system_name,
                                      status="queued", started_by=user, stats={"for_waiting": sorted({r["code"] for r in wanted})})
    tell("hr_sync_reading", f"The agent is reading {connector.system_name} again for the people waiting for HR data: {who}.",
         job_id=job.pk)
    jobs._run_browser_job(job.pk)  # the same read as the "Read HR tool" button, here in the worker
    job.refresh_from_db()
    if job.status == "failed":
        raise RuntimeError(f"Reading {connector.system_name} failed: {job.error}")  # retried by the workflow
    if job.status != "ready":
        tell("hr_sync_needs_mapping", f"{connector.system_name} was read again (job #{job.pk}), but a screen's columns changed: "
             "confirm the field mapping on the HR data page, then build and review the data.", job_id=job.pk)
        return {"status": "needs_mapping", "found": [], "still_missing": [key(r) for r in wanted], "stop": True}
    snapshot = snapshots.create_from_job(job, user)
    if snapshot.has_blocking_issues:
        tell("hr_sync_errors", f"{connector.system_name} was read again (HR data #{snapshot.pk}), but the data has errors. "
             "Open it on the HR data page.", snapshot_id=snapshot.pk)
        return {"status": "data_has_errors", "snapshot_id": snapshot.pk, "found": [], "still_missing": [key(r) for r in wanted],
                "stop": True}
    known = {e["code"] for e in snapshot.data.get("employees", [])}
    incomplete = snapshot.data.get("incomplete") or {}
    found = [r for r in wanted if r["code"] in known and not set(r["missing"]) & set(incomplete.get(r["code"], []))]
    still = [r for r in wanted if r not in found]
    message = f"The agent read {connector.system_name} again (HR data #{snapshot.pk}, waiting for review)."
    if found:
        message += " Found: " + ", ".join(r["code"] for r in found) + ". Review the new HR data, then use it for the month."
    if still:
        message += " Still missing: " + "; ".join(f"{r['code']} ({_labels(incomplete.get(r['code'], r['missing']))})" for r in still) + \
                   ". The agent reads again later."
    tell("hr_sync_read", message, snapshot_id=snapshot.pk)
    return {"status": "read", "snapshot_id": snapshot.pk, "found": [key(r) for r in found], "still_missing": [key(r) for r in still],
            "stop": False}
