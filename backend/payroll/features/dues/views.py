"""Government dues of final months: list them, and record (or undo) their payment."""
from __future__ import annotations

from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from payroll.shared.permissions import RuleViolation, is_in_group

from ..companies.models import Company
from ..companies.present import company as present_company
from . import dues as dues_service
from .models import StatutoryDue
from .present import due


@api_view(["GET"])
def company_dues(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    if not is_in_group(request.user, "checker"):
        raise RuleViolation("Only the finance head can see government dues.", code="forbidden")
    wanted = request.query_params.get("status", "all")
    qs = StatutoryDue.objects.filter(company=company).select_related("paid_by").order_by("due_date", "period", "kind", "jurisdiction")
    if wanted == "open":
        qs = qs.filter(paid_on__isnull=True)
    elif wanted == "paid":
        qs = qs.filter(paid_on__isnull=False)
    items = [due(x) for x in qs]
    everything = StatutoryDue.objects.filter(company=company)
    open_items = [due(x) for x in everything.filter(paid_on__isnull=True)]
    return Response({
        "dues": items,
        "summary": {"open": len(open_items), "overdue": sum(1 for x in open_items if x["status"]["code"] == "overdue"),
                    "open_total": str(sum((x.total for x in everything.filter(paid_on__isnull=True)), 0)),
                    "paid": everything.filter(paid_on__isnull=False).count()},
        "company": present_company(company),
    })


@api_view(["POST"])
def due_action(request, due_id, action):
    get_object_or_404(StatutoryDue, pk=due_id)
    if action == "paid":
        x = dues_service.mark_paid(due_id, request.user, request.data.get("paid_on", ""), request.data.get("reference", ""),
                                   request.data.get("note", ""))
    elif action == "undo":
        x = dues_service.undo_paid(due_id, request.user, request.data.get("reason", ""))
    else:
        raise Http404
    return Response(due(x))
