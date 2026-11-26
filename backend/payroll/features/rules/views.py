from __future__ import annotations

from datetime import date

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from payroll_engine import policies as engine_policies
from payroll_engine.engine import calculate_employee
from payroll_engine.periods import Period

from payroll.shared.permissions import RuleViolation, is_in_group

from ..companies.models import Company
from . import policies as policy_service
from . import present as P
from .models import PolicyVersion


@api_view(["GET", "POST"])
def policies(request, company_id):
    company = get_object_or_404(Company, pk=company_id)
    if request.method == "POST":
        if not (is_in_group(request.user, "maker") or is_in_group(request.user, "checker")):
            raise RuleViolation("You cannot change rules.", code="forbidden")
        d = request.data
        try:
            start = date.fromisoformat(d.get("effective_from", ""))
        except ValueError as exc:
            raise RuleViolation("Give a start date like 2025-08-01.") from exc
        payload = d.get("payload")
        if isinstance(payload, str):
            import json
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise RuleViolation(f"The rule data is not valid JSON: {exc.msg} (line {exc.lineno}).") from exc
        version, warnings = policy_service.add_version(company=company, key=d.get("key", ""), jurisdiction=d.get("jurisdiction", ""),
                                                       effective_from=start, payload=payload, title=d.get("title", ""),
                                                       note=d.get("note", ""), user=request.user)
        return Response({"version": P.policy(version, set()), "warnings": warnings}, status=201)
    used = policy_service.used_version_ids(company)
    # The pay cycle is set per pay group (its own card); the setting it replaced is kept only for months stored before.
    qs = (PolicyVersion.objects.filter(Q(company__isnull=True) | Q(company=company)).exclude(key="pay_cycle")
          .order_by("key", "jurisdiction", "effective_from", "id"))
    groups: dict[tuple, dict] = {}
    for v in qs:
        k = (v.scope, v.key, v.jurisdiction)
        g = groups.setdefault(k, {
            "scope": v.scope, "key": v.key, "jurisdiction": v.jurisdiction,
            "title": engine_policies.STATUTORY_KEYS.get(v.key) or engine_policies.COMPANY_KEYS.get(v.key, v.key),
            "versions": [],
        })
        g["versions"].append(P.policy(v, used))
    for key in engine_policies.COMPANY_KEYS:
        if key == "pay_cycle":
            continue
        if (PolicyVersion.SCOPE_COMPANY, key, "") not in groups:
            groups[(PolicyVersion.SCOPE_COMPANY, key, "")] = {
                "scope": "company", "key": key, "jurisdiction": "", "title": engine_policies.COMPANY_KEYS[key], "versions": [],
                "default": engine_policies.COMPANY_DEFAULTS[key]}
    today = Period.of(timezone.localdate())
    for g in groups.values():
        current = None
        for v in g["versions"]:
            if v["effective_from"] <= today.start.isoformat():
                current = v["id"]
        g["current_version_id"] = current
    return Response({
        "groups": sorted(groups.values(), key=lambda g: (g["scope"] != "statutory", g["key"], g["jurisdiction"])),
        "templates": {k: engine_policies.COMPANY_DEFAULTS.get(k) for k in engine_policies.COMPANY_KEYS},
        "rule_as_of": "Rules and settings are picked as of the 1st day of each pay month.",
    })


@api_view(["DELETE"])
def policy_delete(request, version_id):
    version = get_object_or_404(PolicyVersion, pk=version_id)
    if not (is_in_group(request.user, "maker") or is_in_group(request.user, "checker")):
        raise RuleViolation("You cannot change rules.", code="forbidden")
    policy_service.delete_version(version)
    return Response(status=204)


@api_view(["POST"])
def wage_code_simulator(request, company_id):
    """Stretch goal: Labour Codes 50% wage rule. Every number comes from the engine."""
    from payroll_engine.money import d, to_str

    company = get_object_or_404(Company, pk=company_id)
    data = request.data
    gross = d(data.get("monthly_gross", "60000"))
    basic_share = d(data.get("basic_share", "0.40"))
    hra_share = d(data.get("hra_share", "0.20"))
    basic = (gross * basic_share).quantize(d(1))
    hra = (gross * hra_share).quantize(d(1))
    special = gross - basic - hra
    versions = policy_service.versions_for(company)
    rule = next((v for v in sorted(versions, key=lambda v: v["effective_from"]) if v["key"] == "wage_definition"), None)
    after = Period.parse(data.get("after", "")) if data.get("after") else (
        Period.parse(rule["effective_from"][:7]).next() if rule else Period(2025, 12))
    before = after.prev()

    def run(components, period, pf_base):
        emp = {"code": "SIM", "name": data.get("name", "Nisha Verma"), "work_state": company.state, "gender": "F",
               "compensation": [{"effective_from": "2000-01-01", "components": {k: to_str(v) for k, v in components.items()}}],
               "unpaid_days": "0", "one_time": [], "ytd": {"months": []}, "arrears_basis": []}
        vs = list(versions) + [{"id": f"what-if:pf_policy:{pf_base}", "key": "pf_policy", "scope": "override", "jurisdiction": "",
                                "effective_from": period.start.isoformat(), "title": "PF base (simulation)",
                                "payload": engine_policies.COMPANY_DEFAULTS["pf_policy"] | {"wage_base": pf_base}}]
        r = calculate_employee(emp, period, vs, {"state": company.state})
        a = r["amounts"]
        pf_line = next(x for x in r["lines"] if x["code"] == "pf_employee")
        return {"pf_wage": a["pf_wage"], "pf_employee": a["pf_employee"], "pf_employer": a["pf_employer"], "net_pay": a["net_pay"],
                "employer_cost": a["employer_cost"], "pf_reason": pf_line["reason"], "pf_formula": pf_line["formula"]}

    current = {"basic": basic, "hra": hra, "special_allowance": special}
    half = (gross / 2).quantize(d(1))
    restructured = {"basic": half, "hra": hra, "special_allowance": gross - half - hra}
    options = [
        {"title": "Keep the pay structure (rule adds the excess back to PF wages)", "components": current, "pf_base": "full"},
        {"title": "Raise basic to 50% of pay, take it from special allowance", "components": restructured, "pf_base": "full"},
        {"title": "Keep the structure, work PF on the first ₹15,000 only", "components": current, "pf_base": "capped"},
    ]
    out = []
    for o in options:
        out.append({"title": o["title"], "components": {k: to_str(v) for k, v in o["components"].items()}, "pf_base": o["pf_base"],
                    "before": run(o["components"], before, o["pf_base"]), "after": run(o["components"], after, o["pf_base"])})
    return Response({"employee": data.get("name", "Nisha Verma"), "monthly_gross": to_str(gross), "before": str(before),
                     "after": str(after), "before_label": before.label, "after_label": after.label,
                     "rule": rule and {"id": rule["id"], "effective_from": rule["effective_from"], "title": rule["title"]},
                     "options": out})
