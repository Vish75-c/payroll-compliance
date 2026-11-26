"""Reading and adding versions of rules and company settings."""
from __future__ import annotations

from datetime import date

from django.db import transaction
from django.db.models import Q

from payroll_engine import policies as engine_policies
from payroll_engine.periods import Period

from payroll.shared.permissions import RuleViolation

from ..companies.models import Company
from ..payroll_runs.models import PayrollRun
from .models import PolicyVersion


def versions_for(company: Company) -> list[dict]:
    """All statutory rules plus this company's settings, in the shape the engine expects."""
    qs = PolicyVersion.objects.filter(Q(company__isnull=True) | Q(company=company)).order_by("id")
    return [v.as_engine_dict() for v in qs]


def used_version_ids(company: Company | None = None) -> set[str]:
    """Versions that a submitted, approved or final month has used. These can never be removed."""
    runs = PayrollRun.objects.filter(status__in=[PayrollRun.AWAITING_APPROVAL, PayrollRun.APPROVED, PayrollRun.FINALIZED])
    if company:
        runs = runs.filter(company=company)
    used = set()
    for result in runs.values_list("result", flat=True):
        for ref in (result or {}).get("rules_used", []):
            used.add(ref["version_id"])
    return used


def first_affected_period(effective_from: date) -> Period:
    """Rules are read as of the 1st of the month, so a mid-month start applies from the next month."""
    p = Period.of(effective_from)
    if engine_policies.RULE_AS_OF == "period_start" and effective_from.day != 1:
        p = p.next()
    return p


@transaction.atomic
def add_version(*, company: Company | None, key: str, jurisdiction: str, effective_from: date, payload: dict,
                title: str, note: str, user) -> tuple[PolicyVersion, list[str]]:
    statutory = engine_policies.is_statutory(key)
    if statutory and company is not None:
        company = None  # government rules are shared by every company
    if not statutory and key not in engine_policies.COMPANY_KEYS:
        raise RuleViolation(f"Unknown rule or setting '{key}'.")
    if statutory:
        jurisdiction = (jurisdiction or "IN").upper()
        if key == "professional_tax" and not jurisdiction.startswith("IN-"):
            raise RuleViolation("Professional tax is a state rule: jurisdiction must look like IN-KA.")
    else:
        jurisdiction = ""
        if company is None:
            raise RuleViolation("A company setting needs a company.")
    errors = engine_policies.validate_payload(key, payload)
    if errors:
        raise RuleViolation("The rule data is not valid: " + "; ".join(errors), code="invalid_payload")

    warnings = []
    first = first_affected_period(effective_from)
    if effective_from.day != 1:
        warnings.append(f"This starts mid-month. Rules are read as of the 1st of each month, so it first applies to {first.label}.")
    final_runs = PayrollRun.objects.filter(status=PayrollRun.FINALIZED, run_type=PayrollRun.TYPE_REGULAR)
    if company:
        final_runs = final_runs.filter(company=company)
    affected_final = sorted({r for r in final_runs.values_list("period", flat=True) if Period.parse(r) >= first})
    if affected_final:
        warnings.append("Months already final are not changed: " + ", ".join(affected_final)
                        + ". A draft re-run shows what they would be under this version.")
    version = PolicyVersion.objects.create(
        company=company,
        scope=PolicyVersion.SCOPE_STATUTORY if statutory else PolicyVersion.SCOPE_COMPANY,
        key=key,
        jurisdiction=jurisdiction,
        effective_from=effective_from,
        payload=payload,
        title=title or (engine_policies.STATUTORY_KEYS.get(key) or engine_policies.COMPANY_KEYS.get(key, key)),
        note=note,
        created_by=user,
    )
    return version, warnings


@transaction.atomic
def delete_version(version: PolicyVersion):
    if str(version.pk) in used_version_ids():
        raise RuleViolation("This version was used by a month that is submitted or final, so it cannot be removed. Add a newer version instead.")
    open_runs = PayrollRun.objects.filter(status__in=[PayrollRun.CALCULATED, PayrollRun.REJECTED])
    for run in open_runs:
        if any(ref["version_id"] == str(version.pk) for ref in (run.result or {}).get("rules_used", [])):
            run.status = PayrollRun.DRAFT
            run.result = None
            run.result_hash = ""
            run.save(update_fields=["status", "result", "result_hash", "updated_at"])
    version.delete()
