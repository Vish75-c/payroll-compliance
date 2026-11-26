"""The life of a payroll month. These functions are the workflow's activities.

Every step re-checks the state under a row lock, so a step that is retried (after a crash
or a restart) either does nothing or does exactly the same thing again.

    draft --calculate--> calculated --submit--> awaiting_approval --approve--> approved --finalize--> finalized
      ^                      |                         |
      +---- (changes) -------+                         +--reject--> rejected --calculate/submit--> ...
"""
from __future__ import annotations

from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from payroll_engine import policies as engine_policies
from payroll_engine.engine import CalculationError, calculate_month
from payroll_engine.hashing import sha256
from payroll_engine.money import d, inr
from payroll_engine.periods import Period

from payroll.shared.permissions import RuleViolation, require_role

from ..companies.models import Company
from ..hr_data.models import DataSnapshot
from ..dues import dues
from ..rules.policies import versions_for
from ..pay_groups import service as groups_service
from . import cycle, exports
from .events import record
from .inputs import build_run_input, default_day_count
from .models import ChangeRequest, PayrollRun


def _lock(run_id: int) -> PayrollRun:
    return PayrollRun.objects.select_for_update().select_related("company", "snapshot").get(pk=run_id)


# --------------------------------------------------------------------------- opening a month

def check_data_is_fresh(company: Company, period: Period, snapshot: DataSnapshot) -> None:
    """A month needs HR data read after the month before it was approved (or, if that month is
    not approved yet, after it was opened). An older read cannot contain what HR entered since:
    a raise, new leave or a new joiner would be silently missing.

    Leave and salary in a read are still picked by their dates, so one read that holds several
    months of HR data is fine; what matters is that it was read late enough.
    """
    previous = (PayrollRun.objects.filter(company=company, run_type=PayrollRun.TYPE_REGULAR, period__lt=str(period))
                .exclude(status=PayrollRun.CANCELLED).order_by("-period", "-id").first())
    if previous is None:
        return
    since = previous.finalized_at or previous.created_at
    if snapshot.created_at >= since:
        return
    what = "approved" if previous.finalized_at else "opened"
    read_on = timezone.localtime(snapshot.created_at).strftime("%d %b %Y %H:%M")
    raise RuleViolation(
        f"The newest reviewed HR data (#{snapshot.pk}, read {read_on}) was read before {previous.period_obj.label} was {what}, "
        f"so it may miss what HR entered since. Read the HR tool again (HR data → Read data now), review the new data, "
        f"then open {period.label}.", code="stale_data")
def _check_day_count(value, p: Period) -> int | None:
    if value in (None, ""):
        return None
    try:
        count = int(str(value).strip())
        if str(count) != str(value).strip():
            raise ValueError
    except ValueError as exc:
        raise RuleViolation("The number of days must be a whole number, such as 26 or 30.") from exc
    if not 1 <= count <= 31:
        raise RuleViolation(f"The number of days must be between 1 and 31 (got {count}).")
    return count


def _stored_day_count(company: Company, p: Period, count: int | None) -> int | None:
    """Kept only when it differs from the company setting, so "the company setting" stays the default."""
    if count is None:
        return None
    versions = [v for v in versions_for(company) if Period.of(date.fromisoformat(v["effective_from"][:10])) <= p]
    return None if count == default_day_count(versions, p, cycle.default_windows(company, p)[0].days) else count


@transaction.atomic
def set_day_count(run_id: int, user, value, note: str = "") -> PayrollRun:
    """Change what one day's pay is divided by for this month. A calculated month goes back to being prepared."""
    require_role(user, "maker", "change the number of days of a month")
    run = _lock(run_id)
    if run.is_locked:
        raise RuleViolation(f"This month is {run.get_status_display().lower()}; its number of days cannot change.")
    p = run.period_obj
    before = effective_day_count(run)
    count = _stored_day_count(run.company, p, _check_day_count(value, p))
    run.day_count = count
    if run.status in (PayrollRun.CALCULATED, PayrollRun.REJECTED):
        run.status = PayrollRun.DRAFT
    run.save()
    after = effective_day_count(run)
    record(run, user, "day_count_changed", f"One day's pay is now monthly pay ÷ {after['days']} ({after['source']}); "
           f"was ÷ {before['days']}." + (" Calculate again." if run.input_hash else "") + (f" Note: {note}" if note else ""),
           before=before["days"], after=after["days"])
    return run


def default_for(company: Company, period: str) -> dict:
    """The day count a month would get from the company setting."""
    try:
        p = Period.parse(period)
    except ValueError as exc:
        raise RuleViolation(str(exc)) from exc
    return {**effective_day_count(PayrollRun(company=company, period=str(p))),
            "cycle": cycle.describe(company, p, groups_service.latest_employees(company))}


def effective_day_count(run: PayrollRun) -> dict:
    p = run.period_obj
    if run.day_count:
        return {"days": run.day_count, "source": f"set for {p.label}", "set_for_month": True}
    versions = [v for v in versions_for(run.company) if Period.of(date.fromisoformat(v["effective_from"][:10])) <= p]
    lop = engine_policies.resolve_company(versions, "lop", p).get("divisor", "days_in_month")
    window = cycle.default_windows(run.company, p)[0]
    if lop != "days_in_month":
        source = "company setting"
    elif (window.start, window.end) == (p.start, p.end):
        source = f"days in {p.label.split()[0]}"
    else:
        source = f"days in the pay cycle, {window.label}"
    return {"days": default_day_count(versions, p, window.days), "source": source, "set_for_month": False}


@transaction.atomic
def open_month(company: Company, period: str, user, snapshot: DataSnapshot | None = None,
               knowledge_date: date | None = None, note: str = "", require_fresh_data: bool = False,
               day_count=None) -> PayrollRun:
    require_role(user, "maker", "open a month")
    try:
        p = Period.parse(period)
    except ValueError as exc:
        raise RuleViolation(str(exc)) from exc
    live = PayrollRun.objects.filter(company=company, period=str(p), run_type=PayrollRun.TYPE_REGULAR).exclude(status=PayrollRun.CANCELLED).first()
    if live:
        raise RuleViolation(f"{p.label} is already open (run #{live.pk}, {live.get_status_display().lower()}).", code="exists")
    if snapshot is None:
        snapshot = DataSnapshot.objects.filter(company=company, status="approved").order_by("-reviewed_at", "-id").first()
        if snapshot is None:
            raise RuleViolation("There is no reviewed HR data yet. Read the HR tool or upload a spreadsheet, then review the data.")
    elif snapshot.company_id != company.pk:
        raise RuleViolation("That data belongs to another company.")
    if snapshot.status != "approved":
        raise RuleViolation("This HR data has not been reviewed yet.")
    if require_fresh_data:
        check_data_is_fresh(company, p, snapshot)
    count = _stored_day_count(company, p, _check_day_count(day_count, p))
    try:
        run = PayrollRun.objects.create(
            company=company, period=str(p), run_type=PayrollRun.TYPE_REGULAR, snapshot=snapshot,
            knowledge_date=knowledge_date or timezone.localdate(), note=note, created_by=user, day_count=count,
        )
    except IntegrityError as exc:
        raise RuleViolation(f"{p.label} is already open.", code="exists") from exc
    days = effective_day_count(run)
    record(run, user, "opened", f"{p.label} opened with HR data #{snapshot.pk} ({snapshot.source_system}). "
           f"One day's pay = monthly pay ÷ {days['days']} ({days['source']}).",
           snapshot_id=snapshot.pk, knowledge_date=run.knowledge_date.isoformat(), day_count=days["days"])
    return run


@transaction.atomic
def use_snapshot(run_id: int, snapshot: DataSnapshot, user, require_fresh_data: bool = False) -> PayrollRun:
    require_role(user, "maker", "change the data of a month")
    run = _lock(run_id)
    if run.is_locked:
        raise RuleViolation(f"This month is {run.get_status_display().lower()}; its data cannot change.")
    if snapshot.status != "approved" or snapshot.company_id != run.company_id:
        raise RuleViolation("Choose reviewed HR data of this company.")
    if require_fresh_data:
        check_data_is_fresh(run.company, run.period_obj, snapshot)
    run.snapshot = snapshot
    run.data_reviewed_by = None
    run.data_reviewed_at = None
    if run.status in (PayrollRun.CALCULATED, PayrollRun.REJECTED):
        run.status = PayrollRun.DRAFT
    run.save()
    record(run, user, "data_replaced", f"Now uses HR data #{snapshot.pk}. The finance team employee must review it again.", snapshot_id=snapshot.pk)
    return run


@transaction.atomic
def review_data(run_id: int, user) -> PayrollRun:
    require_role(user, "maker", "review the data")
    run = _lock(run_id)
    if run.is_locked:
        raise RuleViolation("This month is locked.")
    if run.snapshot.has_blocking_issues:
        raise RuleViolation("The HR data has problems marked as errors. Fix them at the source and read the data again.")
    run.data_reviewed_by = user
    run.data_reviewed_at = timezone.now()
    run.save(update_fields=["data_reviewed_by", "data_reviewed_at", "updated_at"])
    record(run, user, "data_reviewed", f"Finance team employee reviewed the HR data (#{run.snapshot_id}) for {run.period_obj.label}.")
    return run


# --------------------------------------------------------------------------- calculation

def compute(run: PayrollRun) -> tuple[dict, str, dict, str]:
    data = build_run_input(run)
    try:
        result = calculate_month(data)
    except CalculationError as exc:
        raise RuleViolation(f"The calculation cannot run: {exc}") from exc
    return data, sha256(data), result, sha256(result)


def check_calculate(run: PayrollRun, user):
    require_role(user, "maker", "calculate a month")
    if run.is_locked:
        raise RuleViolation(f"This month is {run.get_status_display().lower()} and cannot be recalculated.")
    if not run.data_reviewed_at:
        raise RuleViolation("Review the HR data first (step 1).")


@transaction.atomic
def calculate(run_id: int, user) -> PayrollRun:
    run = _lock(run_id)
    check_calculate(run, user)
    data, input_hash, result, result_hash = compute(run)
    same_as_before = bool(run.result_hash) and run.input_hash == input_hash and run.result_hash == result_hash
    run.input, run.input_hash, run.result, run.result_hash = data, input_hash, result, result_hash
    run.calculated_at = timezone.now()
    run.calculated_by = user
    run.calculation_count += 1
    run.status = PayrollRun.CALCULATED
    run.last_error = ""
    run.save()
    msg = f"Calculated {result['employee_count']} employees. Net pay {inr(d(result['totals'].get('net_pay', '0')))}."
    if same_as_before:
        msg += " Same inputs and same result as the previous calculation."
    record(run, user, "calculated", msg, input_hash=input_hash, result_hash=result_hash, same_as_previous=same_as_before)
    return run


def freshness(run: PayrollRun) -> dict:
    """Has anything that feeds this month changed since it was calculated?"""
    if not run.input_hash:
        return {"fresh": False, "reasons": ["not calculated yet"]}
    current = build_run_input(run)
    newer = newer_hr_data(run)
    if sha256(current) == run.input_hash and not newer:
        return {"fresh": True, "reasons": []}
    reasons = []
    if newer:
        reasons.append(f"HR data was read again (#{newer.pk}, reviewed {timezone.localtime(newer.reviewed_at):%d %b %H:%M}) "
                       "and differs for this month")
    stored = run.input or {}
    if current["snapshot"] != stored.get("snapshot"):
        reasons.append("the month now uses different HR data")
    if current["changes"] != stored.get("changes"):
        reasons.append("confirmed changes for the month are different")
    if [v["id"] for v in current["policy_versions"]] != [v["id"] for v in stored.get("policy_versions", [])]:
        reasons.append("a rule or company setting version was added or removed")
    month_set = lambda vs: [v["payload"] for v in vs if v.get("scope") == engine_policies.MONTH]  # noqa: E731
    if month_set(current["policy_versions"]) != month_set(stored.get("policy_versions", [])):
        reasons.append("the number of days for one day's pay was changed")
    if [e["ytd"] for e in current["employees"]] != [e["ytd"] for e in stored.get("employees", [])] and not reasons:
        reasons.append("pay and TDS from earlier in the year (opening balances) changed")
    if current.get("history_months") != stored.get("history_months"):
        reasons.append("earlier months were finalised since")
    if not reasons:
        reasons.append("the inputs differ")
    return {"fresh": False, "reasons": reasons}


def newer_hr_data(run: PayrollRun) -> DataSnapshot | None:
    """A later reviewed snapshot that would change this month's employees, salary or leave."""
    if run.run_type != PayrollRun.TYPE_REGULAR or run.status == PayrollRun.FINALIZED:
        return None
    latest = (DataSnapshot.objects.filter(company=run.company, status="approved", id__gt=run.snapshot_id)
              .order_by("-id").first())
    if latest is None or latest.content_hash == run.snapshot.content_hash:
        return None
    probe = PayrollRun(pk=run.pk, company=run.company, period=run.period, run_type=run.run_type, snapshot=latest,
                       knowledge_date=run.knowledge_date, policy_overrides=run.policy_overrides)
    if build_run_input(probe)["employees"] != build_run_input(run)["employees"]:
        return latest
    return None


def verify_repeatable(run: PayrollRun) -> dict:
    """Calculate again from the stored input (no database reads) and compare."""
    if not run.input:
        raise RuleViolation("Not calculated yet.")
    again = calculate_month(run.input)
    again_hash = sha256(again)
    return {"stored_result_hash": run.result_hash, "recalculated_hash": again_hash, "identical": again_hash == run.result_hash}


# --------------------------------------------------------------------------- maker -> checker

def check_submit(run: PayrollRun, user):
    require_role(user, "maker", "submit a month")
    if run.run_type != PayrollRun.TYPE_REGULAR:
        raise RuleViolation("A draft re-run is a what-if and cannot be submitted.")
    if run.status not in (PayrollRun.CALCULATED, PayrollRun.REJECTED) or not run.result:
        raise RuleViolation("Calculate the month before submitting it.")
    pending = ChangeRequest.objects.filter(company=run.company, period=run.period, status=ChangeRequest.PROPOSED).count()
    if pending:
        raise RuleViolation(f"{pending} proposed change(s) are neither confirmed nor discarded.")
    errors = [i for i in run.result.get("issues", []) if i["level"] == "error"]
    if errors:
        raise RuleViolation("Fix the errors in the calculation first: " + "; ".join(f"{i['employee']}: {i['message']}" for i in errors))
    state = freshness(run)
    if not state["fresh"]:
        raise RuleViolation("Calculate again before submitting: " + ", ".join(state["reasons"]) + ".", code="stale")


@transaction.atomic
def submit(run_id: int, user, note: str = "") -> PayrollRun:
    run = _lock(run_id)
    if run.status == PayrollRun.AWAITING_APPROVAL and run.submitted_by_id == getattr(user, "pk", None):
        return run  # retried step
    check_submit(run, user)
    run.status = PayrollRun.AWAITING_APPROVAL
    run.submitted_by = user
    run.submitted_at = timezone.now()
    run.submitted_result_hash = run.result_hash
    run.submitted_result = run.result
    run.decision_note = ""
    run.save()
    record(run, user, "submitted", f"Submitted for approval. Net pay {inr(d(run.result['totals'].get('net_pay', '0')))}." + (f" Note: {note}" if note else ""),
           result_hash=run.result_hash)
    return run


def check_decide(run: PayrollRun, user):
    require_role(user, "checker", "approve or send back a month")
    if run.status != PayrollRun.AWAITING_APPROVAL:
        raise RuleViolation(f"This month is {run.get_status_display().lower()}, not waiting for approval.")
    if user.pk in (run.submitted_by_id, run.calculated_by_id):
        raise RuleViolation("The finance head must be a different person from the finance team employee who prepared this month.", code="four_eyes")


@transaction.atomic
def approve(run_id: int, user, note: str = "") -> PayrollRun:
    run = _lock(run_id)
    if run.status in (PayrollRun.APPROVED, PayrollRun.FINALIZED) and run.decided_by_id == getattr(user, "pk", None):
        return run  # retried step
    check_decide(run, user)
    if run.result_hash != run.submitted_result_hash:
        raise RuleViolation("The result changed after it was submitted. Send it back.")
    state = freshness(run)
    if not state["fresh"]:
        raise RuleViolation("HR data or rules changed after the finance team employee prepared this month (" + ", ".join(state["reasons"])
                            + "). Send it back so the finance team employee can calculate again.", code="stale")
    check = verify_repeatable(run)
    if not check["identical"]:
        raise RuleViolation("Calculating again from the stored input gave a different result. Send it back.")
    run.status = PayrollRun.APPROVED
    run.decided_by = user
    run.decided_at = timezone.now()
    run.decision_note = note
    run.save()
    record(run, user, "approved", "Approved by the finance head." + (f" Note: {note}" if note else ""), result_hash=run.result_hash)
    return run


@transaction.atomic
def reject(run_id: int, user, note: str) -> PayrollRun:
    run = _lock(run_id)
    if run.status == PayrollRun.REJECTED and run.decided_by_id == getattr(user, "pk", None):
        return run
    check_decide(run, user)
    if not (note or "").strip():
        raise RuleViolation("Say why the month is being sent back.")
    run.status = PayrollRun.REJECTED
    run.decided_by = user
    run.decided_at = timezone.now()
    run.decision_note = note
    run.save()
    record(run, user, "rejected", f"Sent back to the finance team employee: {note}")
    return run


def check_finalize(run: PayrollRun, user):
    """Retrying a finalise that failed after approval (the checker's decision already stands)."""
    require_role(user, "checker", "finalise a month")
    if run.status != PayrollRun.APPROVED:
        raise RuleViolation(f"This month is {run.get_status_display().lower()}; only an approved month that could not be finalised can be finalised again.")


@transaction.atomic
def finalize(run_id: int, user=None) -> PayrollRun:
    run = _lock(run_id)
    if run.status == PayrollRun.FINALIZED:
        return run  # already done; finalising twice is a no-op
    if run.status != PayrollRun.APPROVED:
        raise RuleViolation("Only an approved month can be finalised.")
    try:
        manifest = exports.write_files(run)
    except exports.FilesDoNotMatch as exc:
        # Retrying cannot fix a mismatch: stop, keep the month approved, and say why.
        raise RuleViolation(f"The month was not finalised: {exc}", code="files_mismatch") from exc
    run.files = manifest
    run.status = PayrollRun.FINALIZED
    run.finalized_at = timezone.now()
    run.last_error = ""
    try:
        with transaction.atomic():
            run.save()
    except IntegrityError as exc:
        raise RuleViolation(f"{run.period_obj.label} is already final.") from exc
    owed = dues.create_for_run(run)
    record(run, user, "finalized", f"Final. Register and {len(manifest['files']) - 1} payment files written; all totals match the register. "
           f"{len(owed)} government payment{'s' if len(owed) != 1 else ''} to make: "
           + ", ".join(f"{dues.title(x)} {inr(x.total)} by {x.due_date:%d %b}" for x in owed) + ".",
           files=list(manifest["files"]), dues=[x.pk for x in owed])
    return run


@transaction.atomic
def cancel(run_id: int, user, note: str = "") -> PayrollRun:
    require_role(user, "maker", "cancel a month")
    run = _lock(run_id)
    if run.status == PayrollRun.CANCELLED:
        return run
    if run.status in (PayrollRun.APPROVED, PayrollRun.FINALIZED):
        raise RuleViolation("An approved or final month cannot be cancelled.")
    if run.status == PayrollRun.AWAITING_APPROVAL:
        raise RuleViolation("The month is waiting for the finance head; ask them to send it back first.")
    run.status = PayrollRun.CANCELLED
    run.save(update_fields=["status", "updated_at"])
    dropped = ChangeRequest.objects.filter(run=run, status=ChangeRequest.PROPOSED).update(status=ChangeRequest.REJECTED)
    record(run, user, "cancelled", "Cancelled." + (f" {dropped} unconfirmed change(s) discarded." if dropped else "")
           + (f" {note}" if note else ""))
    return run


# --------------------------------------------------------------------------- what-if

@transaction.atomic
def create_draft_rerun(base: PayrollRun, user, overrides: dict | None = None, note: str = "") -> PayrollRun:
    """Re-run a month as a draft, with today's rules and optional what-if settings. The original stays as it was."""
    require_role(user, "maker", "create a draft re-run")
    overrides = overrides or {}
    for key, values in overrides.items():
        if key not in engine_policies.COMPANY_KEYS:
            raise RuleViolation(f"Only company settings can be changed in a what-if ('{key}' is not one). Add a new rule version instead.")
        merged = engine_policies.resolve_company([], key, base.period_obj).payload | dict(values)
        errors = engine_policies.validate_payload(key, merged)
        if errors:
            raise RuleViolation(f"What-if value for {key} is not valid: " + "; ".join(errors))
    run = PayrollRun.objects.create(
        company=base.company, period=base.period, run_type=PayrollRun.TYPE_DRAFT, snapshot=base.snapshot,
        knowledge_date=base.knowledge_date, base_run=base, policy_overrides=overrides, note=note, created_by=user, day_count=base.day_count,
        data_reviewed_by=base.data_reviewed_by, data_reviewed_at=base.data_reviewed_at, workflow_backend="none",
    )
    record(run, user, "opened", f"Draft re-run of {base.period_obj.label} (compared with run #{base.pk}, which is not changed).",
           base_run=base.pk, overrides=overrides)
    return calculate(run.pk, user)
