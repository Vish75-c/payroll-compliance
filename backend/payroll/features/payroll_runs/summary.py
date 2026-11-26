"""Run summaries for the payroll, company overview, and HR snapshot APIs."""
from __future__ import annotations

from payroll_engine.periods import Period

from payroll.shared.present import _dt

from .models import PayrollRun


def run_label(r: PayrollRun) -> str:
    label = Period.parse(r.period).label
    return f"{label} (draft #{r.pk})" if r.run_type == PayrollRun.TYPE_DRAFT else label


def run_summary(r: PayrollRun):
    totals = (r.result or {}).get("totals", {})
    return {
        "id": r.pk, "period": r.period, "label": run_label(r), "run_type": r.run_type, "status": r.status,
        "status_label": r.get_status_display(), "net_pay": totals.get("net_pay"), "gross": totals.get("gross"),
        "employer_cost": totals.get("employer_cost"),
        "employee_count": (r.result or {}).get("employee_count"), "calculated_at": _dt(r.calculated_at),
        "base_run_id": r.base_run_id, "policy_overrides": r.policy_overrides, "updated_at": _dt(r.updated_at),
        "workflow_backend": r.workflow_backend,
    }
