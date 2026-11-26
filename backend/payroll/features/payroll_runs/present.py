from __future__ import annotations

from payroll_engine.diff import compare_results
from payroll_engine.periods import Period

from payroll.shared.permissions import is_in_group
from payroll.shared.present import _dt, _user

from ..ai.present import ai
from ..companies.present import company
from ..dues.present import due
from ..hr_data.present import snapshot_summary
from . import runs as run_service
from .inputs import _in_period
from .models import ChangeRequest, PayrollRun
from .summary import run_label, run_summary
from .workflow import dispatch


def comparison_base(r: PayrollRun) -> PayrollRun | None:
    if r.run_type == PayrollRun.TYPE_DRAFT:
        return r.base_run if r.base_run and r.base_run.result else None
    prev = str(Period.parse(r.period).prev())
    candidates = PayrollRun.objects.filter(company=r.company, period=prev, run_type=PayrollRun.TYPE_REGULAR).exclude(
        status=PayrollRun.CANCELLED).exclude(result=None)
    return candidates.filter(status=PayrollRun.FINALIZED).first() or candidates.order_by("-id").first()


def change(c: ChangeRequest):
    return {
        "id": c.pk, "period": c.period, "run_id": c.run_id, "text": c.text, "status": c.status, "proposal": c.proposal,
        "ai": ai(c.ai), "created_by": _user(c.created_by), "created_at": _dt(c.created_at), "decided_by": _user(c.decided_by),
        "decided_at": _dt(c.decided_at),
    }


def _month_people(r: PayrollRun) -> list[dict]:
    period = r.period_obj
    return [{"code": e["code"], "name": e.get("name", e["code"]), "work_state": e.get("work_state", "")}
            for e in (r.snapshot.data.get("employees", []) if r.snapshot_id else []) if _in_period(e, period)]


def run_detail(r: PayrollRun, user, with_workflow: bool = True):
    out = run_summary(r)
    is_maker, is_checker = is_in_group(user, "maker"), is_in_group(user, "checker")
    status = r.status
    regular = r.run_type == PayrollRun.TYPE_REGULAR
    pending_changes = ChangeRequest.objects.filter(company=r.company, period=r.period, status=ChangeRequest.PROPOSED).count()
    out.update({
        "company": company(r.company),
        "period_label": Period.parse(r.period).label,
        "knowledge_date": r.knowledge_date.isoformat(),
        "note": r.note,
        "snapshot": snapshot_summary(r.snapshot),
        # Who is in this month's HR data (for picking a person before the month is calculated).
        "people": _month_people(r),
        # Months already paid, for an unpaid absence found after its month was paid.
        "paid_months": [{"period": x.period, "label": x.period_obj.label} for x in PayrollRun.objects.filter(
            company=r.company, run_type=PayrollRun.TYPE_REGULAR, status=PayrollRun.FINALIZED, period__lt=r.period).order_by("-period")],
        "data_reviewed_by": _user(r.data_reviewed_by), "data_reviewed_at": _dt(r.data_reviewed_at),
        "calculated_by": _user(r.calculated_by), "calculation_count": r.calculation_count,
        "input_hash": r.input_hash, "result_hash": r.result_hash,
        "submitted_by": _user(r.submitted_by), "submitted_at": _dt(r.submitted_at), "submitted_result_hash": r.submitted_result_hash,
        "decided_by": _user(r.decided_by), "decided_at": _dt(r.decided_at), "decision_note": r.decision_note,
        "finalized_at": _dt(r.finalized_at), "files": r.files, "last_error": r.last_error,
        "result": r.result,
        "events": [{"at": _dt(e.at), "actor": e.actor, "action": e.action, "message": e.message, "detail": e.detail}
                   for e in r.events.all()],
        "changes": [change(c) for c in ChangeRequest.objects.filter(company=r.company, period=r.period).exclude(
            status=ChangeRequest.REJECTED).order_by("id")],
        "pending_changes": pending_changes,
        "base_run": run_summary(r.base_run) if r.base_run else None,
    })
    base = comparison_base(r)
    out["comparison"] = None
    if base and r.result:
        out["comparison"] = compare_results(base.result, r.result, run_label(base), run_label(r))
        out["comparison"]["base_run_id"] = base.pk
        out["comparison"]["base_status"] = base.status
    unreviewed = not r.data_reviewed_at
    newer = run_service.newer_hr_data(r) if regular and status not in (PayrollRun.FINALIZED, PayrollRun.CANCELLED) else None
    out["newer_hr_data"] = snapshot_summary(newer) if newer else None
    if regular and status in (PayrollRun.CALCULATED, PayrollRun.REJECTED, PayrollRun.AWAITING_APPROVAL) and r.input_hash:
        out["freshness"] = run_service.freshness(r)
    else:
        out["freshness"] = None
    locked = r.is_locked
    out["can"] = {
        "review_data": is_maker and not locked and unreviewed,
        "add_changes": is_maker and not locked and regular,
        "calculate": is_maker and not locked and not unreviewed,
        "submit": is_maker and regular and status in (PayrollRun.CALCULATED, PayrollRun.REJECTED) and not pending_changes,
        "approve": is_checker and regular and status == PayrollRun.AWAITING_APPROVAL
                   and user.pk not in (r.submitted_by_id, r.calculated_by_id),
        "reject": is_checker and regular and status == PayrollRun.AWAITING_APPROVAL
                  and user.pk not in (r.submitted_by_id, r.calculated_by_id),
        "finalize": is_checker and regular and status == PayrollRun.APPROVED,
        "cancel": is_maker and status in (PayrollRun.DRAFT, PayrollRun.CALCULATED, PayrollRun.REJECTED),
        "draft_rerun": is_maker and bool(r.result),
        "use_latest_data": is_maker and not locked and bool(newer),
        "change_day_count": is_maker and not locked and regular,
    }
    out["day_count"] = run_service.effective_day_count(r)
    out["dues"] = [due(x) for x in r.dues.all()] if is_checker else []  # the finance head's only
    out["four_eyes_note"] = ""
    if status == PayrollRun.AWAITING_APPROVAL and is_checker and user.pk in (r.submitted_by_id, r.calculated_by_id):
        out["four_eyes_note"] = "You prepared this month, so a different person must approve it."
    order = [PayrollRun.DRAFT, PayrollRun.CALCULATED, PayrollRun.AWAITING_APPROVAL, PayrollRun.APPROVED, PayrollRun.FINALIZED]
    rank = order.index(status) if status in order else (1 if status == PayrollRun.REJECTED else 0)
    out["steps"] = [
        {"key": "data", "label": "Review HR data", "done": not unreviewed},
        {"key": "changes", "label": "Changes", "done": not unreviewed and pending_changes == 0},
        {"key": "calculate", "label": "Calculate", "done": rank >= 1 and status != PayrollRun.DRAFT},
        {"key": "submit", "label": "Submit", "done": rank >= 2},
        {"key": "approve", "label": "Finance head approves", "done": rank >= 3},
        {"key": "files", "label": "Register & files", "done": rank >= 4},
    ] if regular else []
    if with_workflow:
        out["workflow"] = dispatch.describe(r)
    else:
        out["workflow"] = {"backend": r.workflow_backend or "local", "workflow_id": r.workflow_id}
    return out
