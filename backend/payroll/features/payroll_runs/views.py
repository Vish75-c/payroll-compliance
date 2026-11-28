from __future__ import annotations

from datetime import date
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from payroll_engine.diff import compare_results

from payroll.shared.permissions import RuleViolation

from ..ai import present as ai_present
from ..ai import tasks as ai_tasks
from ..companies.models import Company
from ..hr_data.models import DataSnapshot
from . import absences as absence_service
from . import changes as change_service
from . import employees as employee_service
from . import exports
from . import present as P
from . import runs as run_service
from .models import ChangeRequest, PayrollRun
from .summary import run_label, run_summary
from .workflow import dispatch


@api_view(["GET", "POST"])
def runs(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    if request.method == "POST":
        snapshot = None
        if request.data.get("snapshot_id"):
            snapshot = get_object_or_404(DataSnapshot, pk=request.data["snapshot_id"])
        knowledge = request.data.get("knowledge_date")
        # One unit: if the workflow cannot be started, the month is not opened either, so it can be
        # opened again once Temporal is back (instead of existing without a workflow).
        with transaction.atomic():
            run = run_service.open_month(company, request.data.get("period", ""), request.user, snapshot=snapshot,
                                         knowledge_date=date.fromisoformat(knowledge) if knowledge else None,
                                         note=request.data.get("note", ""), require_fresh_data=True,
                                         day_count=request.data.get("day_count"))
            dispatch.start(run)
        return Response(P.run_detail(run, request.user), status=201)
    qs = PayrollRun.objects.filter(company=company).order_by("-period", "-id")
    return Response([run_summary(r) for r in qs])


@api_view(["GET"])
def run_detail(request, run_id):
    run = get_object_or_404(PayrollRun.objects.select_related("company", "snapshot"), pk=run_id)
    return Response(P.run_detail(run, request.user, with_workflow=request.query_params.get("workflow") != "0"))


@api_view(["GET"])
def run_possible_absences(request, run_id):
    """Working days with no check-in and no approved leave: suggestions for the maker, never a pay cut by themselves."""
    run = get_object_or_404(PayrollRun.objects.select_related("company", "snapshot"), pk=run_id)
    return Response(absence_service.possible_absences(run))


@api_view(["POST"])
def run_dismiss_absence(request, run_id):
    run = get_object_or_404(PayrollRun.objects.select_related("company", "snapshot"), pk=run_id)
    absence_service.dismiss(run, request.user, request.data.get("employee_code", ""), request.data.get("dates") or [],
                            request.data.get("reason", ""))
    return Response(absence_service.possible_absences(run))


@api_view(["GET"])
def day_count(request, company_id):
    """What one day's pay is divided by for a month under the company setting (the default when opening it)."""
    company = get_object_or_404(Company, pk=company_id)
    return Response(run_service.default_for(company, request.query_params.get("period", "")))


@api_view(["POST"])
def run_action(request, run_id, action):
    run = get_object_or_404(PayrollRun, pk=run_id)
    note = request.data.get("note", "") or ""
    if action == "review-data":
        run_service.review_data(run.pk, request.user)
        outcome = {"mode": "direct", "queued": False}
    elif action == "use-latest-data":
        latest = DataSnapshot.objects.filter(company=run.company, status="approved").order_by("-reviewed_at", "-id").first()
        if not latest:
            raise RuleViolation("No reviewed HR data.")
        run_service.use_snapshot(run.pk, latest, request.user, require_fresh_data=True)
        dispatch.notify_data_changed(run)
        outcome = {"mode": "direct", "queued": False}
    elif action == "day-count":
        run_service.set_day_count(run.pk, request.user, request.data.get("day_count"), note)
        outcome = {"mode": "direct", "queued": False}
    elif action in ("calculate", "submit", "approve", "reject", "cancel", "finalize"):
        if run.run_type == PayrollRun.TYPE_DRAFT:
            if action == "calculate":
                run_service.calculate(run.pk, request.user)
            elif action == "cancel":
                run_service.cancel(run.pk, request.user)
            else:
                raise RuleViolation("A draft re-run is a what-if; it cannot be submitted or approved.")
            outcome = {"mode": "direct", "queued": False}
        else:
            outcome = dispatch.send(run, action, request.user, note)
    else:
        raise Http404
    run.refresh_from_db()
    return Response({"outcome": outcome, "run": P.run_detail(run, request.user, with_workflow=False)})


@api_view(["POST"])
def run_hold(request, run_id):
    """Put a person who is waiting for HR data on hold, so the month goes ahead for everyone else."""
    run = get_object_or_404(PayrollRun.objects.select_related("company", "snapshot"), pk=run_id)
    employee_service.hold(run.pk, request.data.get("employee_code", ""), request.user, request.data.get("reason", ""))
    run.refresh_from_db()
    dispatch.notify_data_changed(run)
    return Response({"run": P.run_detail(run, request.user, with_workflow=False)})


@api_view(["POST"])
def run_verify(request, run_id):
    run = get_object_or_404(PayrollRun, pk=run_id)
    return Response(run_service.verify_repeatable(run))


@api_view(["POST"])
def run_draft(request, run_id):
    base = get_object_or_404(PayrollRun, pk=run_id)
    if not base.result:
        raise RuleViolation("Calculate the month first.")
    draft = run_service.create_draft_rerun(base, request.user, request.data.get("overrides") or {}, request.data.get("note", ""))
    return Response(P.run_detail(draft, request.user, with_workflow=False), status=201)


@api_view(["GET"])
def run_compare(request, run_id, other_id):
    a = get_object_or_404(PayrollRun, pk=other_id)
    b = get_object_or_404(PayrollRun, pk=run_id)
    if not a.result or not b.result:
        raise RuleViolation("Both runs must be calculated.")
    return Response(compare_results(a.result, b.result, run_label(a), run_label(b)))


@api_view(["POST"])
def run_explain(request, run_id):
    """AI writes a plain-words summary of the deterministic comparison. It changes nothing."""
    run = get_object_or_404(PayrollRun, pk=run_id)
    base = P.comparison_base(run)
    if not base or not run.result:
        raise RuleViolation("Nothing to compare with yet.")
    diff = compare_results(base.result, run.result, run_label(base), run_label(run))
    interaction = ai_tasks.summarize_changes(company=run.company, diff=diff, before_label=run_label(base), after_label=run_label(run),
                                             force=bool(request.data.get("force")))
    return Response(ai_present.ai(interaction))


@api_view(["GET"])
def run_file(request, run_id, name):
    run = get_object_or_404(PayrollRun, pk=run_id)
    if name not in (run.files or {}).get("files", {}) and name != "manifest.json":
        raise Http404
    path = Path(settings.MEDIA_ROOT) / run.files["folder"] / name
    if not path.exists():
        raise Http404
    return FileResponse(open(path, "rb"), as_attachment=True, filename=f"{run.company.code}-{run.period}-{name}")
#

@api_view(["GET"])
def run_register_preview(request, run_id):
    run = get_object_or_404(PayrollRun, pk=run_id)
    if not run.result:
        raise Http404
    files = exports.build_files(run.result, run.input)
    name = request.query_params.get("file", "register.csv")
    if name not in files:
        raise Http404
    label = "DRAFT-" if run.status != PayrollRun.FINALIZED else ""
    resp = HttpResponse(files[name], content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{label}{run.company.code}-{run.period}-run{run.pk}-{name}"'
    return resp


@api_view(["POST"])
def run_instruction(request, run_id):
    run = get_object_or_404(PayrollRun, pk=run_id)
    change = change_service.propose(run, request.data.get("text", ""), request.user, force=bool(request.data.get("force")))
    return Response(P.change(change), status=201)


@api_view(["POST"])
def run_add_by_hand(request, run_id):
    """Unpaid absence, a one-time payment or a deduction, entered in a form (no AI)."""
    run = get_object_or_404(PayrollRun, pk=run_id)
    change = change_service.add_by_hand(run, request.user, request.data)
    return Response(P.change(change), status=201)


@api_view(["POST"])
def change_decide(request, change_id, decision):
    change = get_object_or_404(ChangeRequest, pk=change_id)
    if decision == "confirm":
        change = change_service.confirm(change, request.user)
    elif decision == "discard":
        change = change_service.reject(change, request.user)
    else:
        raise Http404
    return Response(P.change(change))
