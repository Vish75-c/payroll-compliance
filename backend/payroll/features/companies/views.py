from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from payroll_engine.periods import Period

from payroll.shared.permissions import RuleViolation, is_in_group

from ..ai import client as ai_client
from ..dues.models import StatutoryDue
from ..dues.present import due
from ..hr_data.models import DataSnapshot
from ..hr_data.present import snapshot_summary
from ..onboarding import onboarding
from ..payroll_runs import runs as run_service
from ..payroll_runs.models import PayrollRun
from ..payroll_runs.summary import run_summary
from . import present as P
from .models import Company


@api_view(["GET", "POST"])
def companies(request):
    if request.method == "POST":
        company = onboarding.create_company(request.user, request.data)
        return Response(P.company(company, detail=True), status=201)
    return Response([P.company(c) for c in Company.objects.order_by("created_at", "id")])


@api_view(["GET", "PATCH"])
def company_detail(request, company_id):
    """The company: its details and registration numbers, and whether its HR data may be sent to an AI model
    (a maker decides; who and when is kept)."""
    company = get_object_or_404(Company, pk=company_id)
    if request.method == "PATCH":
        if not is_in_group(request.user, "maker"):
            raise RuleViolation("Only a finance team employee can change the company.", code="forbidden")
        if "ai_mode" in request.data:
            mode = request.data.get("ai_mode")
            if mode not in (Company.AI_ALLOWED, Company.AI_OFF):
                raise RuleViolation("ai_mode must be 'allowed' or 'off'.")
            if mode != company.ai_mode:
                company.ai_mode, company.ai_mode_changed_by, company.ai_mode_changed_at = mode, request.user, timezone.now()
                company.save(update_fields=["ai_mode", "ai_mode_changed_by", "ai_mode_changed_at"])
        onboarding.update_company(company, request.user, {k: v for k, v in request.data.items() if k in onboarding.DETAILS})
    return Response(P.company(company, detail=True))


@api_view(["GET"])
def states(request):
    return Response(onboarding.states_list())


_STEP_RANK = {PayrollRun.DRAFT: 0, PayrollRun.REJECTED: 0, PayrollRun.CALCULATED: 1, PayrollRun.AWAITING_APPROVAL: 2,
              PayrollRun.APPROVED: 3, PayrollRun.FINALIZED: 4}


def _month_facts(run: PayrollRun) -> dict:
    """What the dashboard shows about one month: its pay period, who joined or left, unpaid days, changes and progress.
    Read from the month's frozen input and result; nothing is recalculated."""
    p = run.period_obj
    start, end = p.start.isoformat(), p.end.isoformat()
    people = (run.input or {}).get("employees", [])
    in_month = lambda d: bool(d) and start <= str(d)[:10] <= end  # noqa: E731
    result_people = (run.result or {}).get("employees", [])
    rank = _STEP_RANK.get(run.status, 0)
    return {
        "pay_period": {"from": start, "to": end},
        "joined": sum(1 for e in people if in_month(e.get("date_of_joining"))),
        "left": sum(1 for e in people if in_month(e.get("date_of_exit"))),
        "with_unpaid_days": sum(1 for e in result_people if float(e.get("amounts", {}).get("unpaid_days") or 0) > 0),
        "changes": len((run.input or {}).get("changes", [])),
        "day_count": run_service.effective_day_count(run),
        "steps": {"data_reviewed": bool(run.data_reviewed_at) or rank >= 1, "calculated": rank >= 1 and bool(run.result),
                  "submitted": rank >= 2, "approved": rank >= 3, "final": rank >= 4},
    }


@api_view(["GET"])
def company_overview(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    runs = list(PayrollRun.objects.filter(company=company).exclude(status=PayrollRun.CANCELLED).select_related("snapshot"))
    months = {}
    for r in sorted(runs, key=lambda r: (r.period, r.id)):
        entry = months.setdefault(r.period, {"period": r.period, "label": Period.parse(r.period).label, "regular": None, "drafts": []})
        if r.run_type == PayrollRun.TYPE_REGULAR:
            entry["regular"] = run_summary(r) | {"facts": _month_facts(r)}
        else:
            entry["drafts"].append(run_summary(r))
    latest_snapshot = DataSnapshot.objects.filter(company=company).order_by("-id").first()
    finals = sorted(r.period for r in runs if r.status == PayrollRun.FINALIZED and r.run_type == PayrollRun.TYPE_REGULAR)
    next_period = str(Period.parse(finals[-1]).next()) if finals else ""
    approved = company.snapshots.filter(status="approved").order_by("-reviewed_at", "-id").first()
    workforce = None
    if approved:
        # Who is on the rolls, from the HR data in use: shown on the overview, never used for pay.
        people = approved.data.get("employees", [])
        fy_start = Period.parse(next_period or str(Period.of(date.today()))).fy_first.start.isoformat()
        count = lambda key: sorted(Counter(e.get(key) or "Not given" for e in people).items(), key=lambda kv: (-kv[1], kv[0]))  # noqa: E731
        workforce = {"total": len(people), "by_department": count("department"), "by_state": count("work_state"),
                     "joined_this_year": sum(1 for e in people if (e.get("date_of_joining") or "") >= fy_start),
                     "financial_year_from": fy_start}
    open_dues = list(StatutoryDue.objects.filter(company=company, paid_on__isnull=True).order_by("due_date", "period", "kind", "jurisdiction"))
    return Response({
        "company": P.company(company),
        "months": [months[k] for k in sorted(months, reverse=True)],
        "next_period": next_period,
        # Why the next month cannot be opened yet (an earlier month is not final), or "".
        "next_blocked": run_service.previous_month_block(company, Period.parse(next_period)) if next_period else "",
        "latest_snapshot": snapshot_summary(latest_snapshot) if latest_snapshot else None,
        "approved_snapshot": snapshot_summary(company.snapshots.filter(status="approved").order_by("-reviewed_at", "-id").first())
        if company.snapshots.filter(status="approved").exists() else None,
        "employee_count": company.employees.filter(active=True).count(),
        "workforce": workforce,
        "awaiting_approval": [run_summary(r) for r in runs if r.status == PayrollRun.AWAITING_APPROVAL],
        "ai": ai_client.describe(),
        "workflow_backend": settings.WORKFLOW_BACKEND,
        "temporal_ui": settings.TEMPORAL_UI_URL if settings.WORKFLOW_BACKEND == "temporal" else "",
        # Government dues are shown to the finance head (checker role) only.
        "dues": {"next": [due(x) for x in open_dues[:3]], "open": len(open_dues),
                 "overdue": sum(1 for x in open_dues if x.due_date < timezone.localdate()),
                 "due_soon": sum(1 for x in open_dues if timezone.localdate() <= x.due_date <= timezone.localdate() + timedelta(days=7)),
                 "due_soon_total": str(sum((x.total for x in open_dues if timezone.localdate() <= x.due_date <= timezone.localdate() + timedelta(days=7)), 0)),
                 "overdue_total": str(sum((x.total for x in open_dues if x.due_date < timezone.localdate()), 0))}
        if is_in_group(request.user, "checker") else None,
        # Approved months whose files could not be written yet: the finance head can finalise them again.
        "approved_waiting": [run_summary(r) for r in runs if r.status == PayrollRun.APPROVED and r.run_type == PayrollRun.TYPE_REGULAR],
        "next_day_count": run_service.default_for(company, next_period) if next_period else None,
        "setting_up": not any(r.run_type == PayrollRun.TYPE_REGULAR for r in runs),
    })
