"""Setting up a new company: where it is in the steps, and its opening balances."""
from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from payroll_engine.periods import Period

from payroll.shared.permissions import RuleViolation

from ..companies.models import Company
from ..companies.present import company as present_company
from . import onboarding


@api_view(["GET"])
def company_onboarding(request, company_id):
    """Where a new company is in being set up, worked out from what exists."""
    company = get_object_or_404(Company, pk=company_id)
    return Response({"company": present_company(company, detail=True), "steps": onboarding.state(company),
                     "states": onboarding.states_list(), "professional_tax": onboarding.professional_tax_for(company.state)})


@api_view(["GET", "POST"])
def opening_balances(request, company_id):
    """Pay and TDS of each employee earlier in the financial year, before the company's first month here."""
    company = get_object_or_404(Company, pk=company_id)
    period = request.data.get("period") if request.method == "POST" else request.query_params.get("period", "")
    if request.method == "GET" and request.query_params.get("template"):
        response = HttpResponse(onboarding.template_csv(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="opening_balances_template.csv"'
        return response
    if request.method == "POST":
        upload = request.FILES.get("file")
        rows = request.data.get("rows") if not upload else None
        return Response(onboarding.save_opening_balances(company, request.user, period, rows=rows,
                                                         file_name=upload.name if upload else "",
                                                         content=upload.read() if upload else b""))
    try:
        first = Period.parse(period)
    except ValueError as exc:
        raise RuleViolation(str(exc)) from exc
    return Response(onboarding.opening_summary(company, first))
