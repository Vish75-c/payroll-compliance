"""From raw tables to a reviewed snapshot of HR data."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from payroll_engine import policies as engine_policies
from payroll_engine.hashing import sha256
from payroll_engine.periods import Period

from payroll.shared.permissions import RuleViolation, require_role

from ..rules.policies import versions_for
from .models import DataSnapshot, Employee, IngestionJob
from .normalize import build_snapshot, revision_key


@transaction.atomic
def create_from_job(job: IngestionJob, user) -> DataSnapshot:
    tables = list(job.tables.select_related("mapping"))
    if not tables:
        raise RuleViolation("Nothing was read.")
    unmapped = [t.name for t in tables if t.mapping is None or not t.mapping.confirmed_at]
    if unmapped:
        raise RuleViolation("Confirm the field mapping first for: " + ", ".join(unmapped))
    today = timezone.localdate()
    structure = engine_policies.resolve_company(versions_for(job.company), "salary_structure", Period.of(today)).payload
    seen_before: dict[str, str] = {}
    for earlier in DataSnapshot.objects.filter(company=job.company).order_by("id"):
        for row in earlier.data.get("compensation", []):
            if row.get("recorded_on"):
                seen_before.setdefault(revision_key(row["employee_code"], row["effective_from"], row["components"]), row["recorded_on"])
    data, checks = build_snapshot(
        [
            {"name": t.name, "headers": t.headers, "rows": t.rows, "mapping": t.mapping.mapping, "value_maps": t.mapping.value_maps,
             "options": t.mapping.options, "read_info": t.read_info, "source_url": t.source_url, "dataset_hint": t.dataset_hint}
            for t in tables
        ],
        structure,
        first_seen=today,
        seen_before=seen_before,
        work_calendar=engine_policies.resolve_company(versions_for(job.company), "work_calendar", Period.of(today)).payload,
    )
    checks = list((job.stats or {}).get("checks", [])) + checks
    from ..pay_groups import service as pay_groups
    unknown = pay_groups.unknown_group_values(data["employees"], pay_groups.groups_for(job.company, Period.of(today)))
    if unknown:
        checks.append({"level": "warning", "code": "unknown_pay_group",
                       "message": f"Pay group {', '.join(repr(u) for u in unknown)} in the HR data is not set up on Rules & settings "
                                  "→ Pay groups; those people are put in a group by the group rules (or the default group)."})
    data["source"] = {"kind": job.kind, "system": job.source_system, "job_id": job.pk, "read_at": (job.finished_at or timezone.now()).isoformat()}
    content = content_of(data)
    snapshot = DataSnapshot.objects.create(
        company=job.company, job=job, source_kind=job.kind, source_system=job.source_system, data=data,
        content_hash=sha256(content), checks=checks,
    )
    job.status = "done"
    job.save(update_fields=["status"])
    return snapshot


# Where a fact came from, or how its date was found, is not part of the fact itself.
INFO_ONLY = {"source", "department", "designation", "recorded_on_assumed"}


def content_of(data: dict) -> dict:
    """The HR facts only, without where each row came from, so the same facts read from a
    spreadsheet and from an HR tool's screens hash the same. Fields shown only for information
    (department, designation) are left out too."""
    content = {}
    for key, value in data.items():
        if key in ("source", "sources"):
            continue
        if isinstance(value, list):
            value = [{k: v for k, v in row.items() if k not in INFO_ONLY} if isinstance(row, dict) else row for row in value]
        content[key] = value
    return content


@transaction.atomic
def approve(snapshot: DataSnapshot, user, note: str = "") -> DataSnapshot:
    require_role(user, "maker", "review HR data")
    if snapshot.status == "approved":
        return snapshot
    if snapshot.has_blocking_issues:
        raise RuleViolation("This data has errors (shown in red). Fix them in the HR tool or the spreadsheet and read it again.")
    snapshot.status = "approved"
    snapshot.reviewed_by = user
    snapshot.reviewed_at = timezone.now()
    snapshot.review_note = note
    snapshot.save()
    refresh_employees(snapshot)
    # People's pay groups follow the reviewed data (a "Pay group" column, or the groups' department/state rules).
    from ..pay_groups import service as pay_groups  # here: pay groups read the months, which read this module's models
    pay_groups.refresh_from_data(snapshot.company, snapshot.data.get("employees", []))
    return snapshot


@transaction.atomic
def reject(snapshot: DataSnapshot, user, note: str = "") -> DataSnapshot:
    require_role(user, "maker", "review HR data")
    if snapshot.status == "approved" and snapshot.runs.exists():
        raise RuleViolation("This data is already used by a month.")
    snapshot.status = "rejected"
    snapshot.reviewed_by = user
    snapshot.reviewed_at = timezone.now()
    snapshot.review_note = note
    snapshot.save()
    return snapshot


def refresh_employees(snapshot: DataSnapshot):
    company = snapshot.company
    codes = set()
    for e in snapshot.data["employees"]:
        codes.add(e["code"])
        Employee.objects.update_or_create(
            company=company, code=e["code"],
            defaults={
                "name": e.get("name", e["code"]),
                "work_state": e.get("work_state", "") or "",
                "gender": e.get("gender", "") or "",
                "date_of_joining": e.get("date_of_joining"),
                "date_of_exit": e.get("date_of_exit"),
                "active": True,
            },
        )
    Employee.objects.filter(company=company).exclude(code__in=codes).update(active=False)
