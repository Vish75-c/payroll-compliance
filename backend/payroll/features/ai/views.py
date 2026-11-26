from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..companies.models import Company
from . import client as ai_client
from . import present as P
from .models import AIInteraction


@api_view(["GET"])
def ai_log(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    qs = AIInteraction.objects.filter(Q(company=company) | Q(company__isnull=True)).order_by("-created_at")[:200]
    return Response({"describe": ai_client.describe(), "items": [P.ai(i, full=True) for i in qs]})
