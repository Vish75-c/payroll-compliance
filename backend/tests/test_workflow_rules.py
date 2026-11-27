"""Maker/checker, repeatability, stale data, finalise-once, and rule changes as data edits."""
from datetime import date

import pytest

from payroll.models import PayrollRun
from payroll.features.payroll_runs import changes, runs
from payroll.features.rules import policies
from payroll.demo import setup
from payroll.features.hr_data import snapshots
from payroll.shared.permissions import RuleViolation

pytestmark = pytest.mark.django_db


def history(company):
    for m in ("2025-04", "2025-05", "2025-06"):
        setup.run_month(company, m, log=lambda *a: None)


def prepare_july(company, maker):
    run = runs.open_month(company, "2025-07", maker)
    runs.review_data(run.pk, maker)
    ch = changes.propose(run, "Give Priya a ₹10,000 one-time bonus this month", maker)
    changes.confirm(ch, maker)
    return runs.calculate(run.pk, maker)


def emp(run, code):
    return next(e for e in run.result["employees"] if e["code"] == code)


def test_full_month_with_four_eyes(demo):
    company, _ = demo
    history(company)
    maker, checker = setup.system_user("maker"), setup.system_user("checker")
    run = prepare_july(company, maker)
    assert emp(run, "BL001")["amounts"]["net_pay"] == "118000"
    runs.submit(run.pk, maker)
    with pytest.raises(RuleViolation):  # a maker cannot approve
        runs.approve(run.pk, maker)
    both = setup.system_user("checker")
    both.groups.add(*setup.system_user("maker").groups.all())
    run.refresh_from_db()
    run.calculated_by = both
    run.save()
    with pytest.raises(RuleViolation, match="different person"):
        runs.approve(run.pk, both)
    run.calculated_by = maker
    run.save()
    runs.approve(run.pk, checker, "ok")
    run = runs.finalize(run.pk, checker)
    assert run.status == PayrollRun.FINALIZED
    assert all(c["matches"] for c in run.files["reconciliation"])
    assert runs.finalize(run.pk, checker).finalized_at == run.finalized_at  # finalising twice changes nothing
    with pytest.raises(RuleViolation):
        runs.open_month(company, "2025-07", maker)


def test_running_july_twice_gives_the_same_result(demo):
    company, _ = demo
    history(company)
    maker = setup.system_user("maker")
    first = prepare_july(company, maker)
    second = runs.calculate(first.pk, maker)
    assert second.result_hash == first.result_hash
    assert runs.verify_repeatable(second)["identical"]


def test_rejection_and_stale_hr_data(demo):
    company, snapshot = demo
    history(company)
    maker, checker = setup.system_user("maker"), setup.system_user("checker")
    run = prepare_july(company, maker)
    runs.submit(run.pk, maker)
    # HR changes Arjun's leave after the maker prepared July: read again and review the new data
    data = dict(snapshot.data)
    data["leave"] = [dict(r, days="1", end_date=r["start_date"]) if r["employee_code"] == "BL002" else r for r in data["leave"]]
    from payroll.models import DataSnapshot
    from payroll_engine.hashing import sha256
    newer = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet", data=data,
                                        content_hash=sha256(snapshots.content_of(data)), checks=[])
    snapshots.approve(newer, maker)
    with pytest.raises(RuleViolation, match="HR data"):
        runs.approve(run.pk, checker)
    runs.reject(run.pk, checker, "HR updated Arjun's leave")
    runs.use_snapshot(run.pk, newer, maker)
    runs.review_data(run.pk, maker)
    run = runs.calculate(run.pk, maker)
    assert emp(run, "BL002")["amounts"]["unpaid_days"] == "1"
    runs.submit(run.pk, maker)
    runs.approve(run.pk, checker)


def test_change_setting_changes_arjun_and_says_why(demo):
    company, _ = demo
    history(company)
    maker = setup.system_user("maker")
    july = prepare_july(company, maker)
    draft = runs.create_draft_rerun(july, maker, {"lop": {"divisor": 30}})
    assert emp(draft, "BL002")["amounts"]["lop_deduction"] == "1800"
    july.refresh_from_db()
    assert emp(july, "BL002")["amounts"]["lop_deduction"] == "1742"  # the original is untouched
    lop = next(l for l in emp(draft, "BL002")["lines"] if l["code"] == "lop_deduction")
    assert "30 days" in lop["reason"] and any(r["scope"] == "override" for r in lop["rules"])


def test_new_professional_tax_rule_is_a_data_edit(demo):
    company, _ = demo
    history(company)
    maker, checker = setup.system_user("maker"), setup.system_user("checker")
    july = prepare_july(company, maker)
    runs.submit(july.pk, maker)
    runs.approve(july.pk, checker)
    july = runs.finalize(july.pk, checker)
    june = PayrollRun.objects.get(company=company, period="2025-06", status="finalized")
    policies.add_version(company=None, key="professional_tax", jurisdiction="IN-KA", effective_from=date(2025, 7, 1),
                         payload={"slabs": [{"min": "0", "amount": "0"}, {"min": "15000", "amount": "150"}, {"min": "25000", "amount": "300"}]},
                         title="Made-up KA PT from July 2025", note="", user=maker)
    draft = runs.create_draft_rerun(july, maker)
    assert emp(draft, "BL002")["amounts"]["pt"] == "150"  # 16,258 is now in the ₹150 slab
    assert emp(draft, "BL003")["amounts"]["pt"] == "300"
    july.refresh_from_db()
    assert emp(july, "BL003")["amounts"]["pt"] == "200"  # the approved July stays as it was
    june_draft = runs.create_draft_rerun(june, maker)
    assert june_draft.result_hash == june.result_hash  # months before the start date are the same


def test_versions_used_by_final_month_cannot_be_removed(demo):
    company, _ = demo
    history(company)
    from payroll.models import PolicyVersion
    pf = PolicyVersion.objects.get(key="pf")
    with pytest.raises(RuleViolation):
        policies.delete_version(pf)
    with pytest.raises(ValueError):
        pf.title = "edited"
        pf.save()


def test_a_month_opens_only_after_the_months_before_are_final(demo):
    company, _ = demo
    history(company)  # April to June, final
    maker = setup.system_user("maker")
    with pytest.raises(RuleViolation, match="Run July 2025 first"):  # no skipping a month
        runs.open_month(company, "2025-08", maker)
    july = prepare_july(company, maker)  # open, calculated, not final
    with pytest.raises(RuleViolation, match="once July 2025 is done") as exc:
        runs.open_month(company, "2025-08", maker)
    assert exc.value.code == "previous_not_done"
    assert runs.previous_month_block(company, july.period_obj.next())
    PayrollRun.objects.filter(pk=july.pk).update(status=PayrollRun.FINALIZED)
    assert runs.previous_month_block(company, july.period_obj.next()) == ""
    assert runs.open_month(company, "2025-08", maker).period == "2025-08"


def test_run_employee_keeps_one_state_per_person_per_month(demo):
    from django.db import IntegrityError, transaction

    from payroll.models import RunEmployee

    company, _ = demo
    run = runs.open_month(company, "2025-04", setup.system_user("maker"))
    row = RunEmployee.objects.create(run=run, employee_code="BL016", status=RunEmployee.WAITING, missing=["work_state"])
    assert row.get_status_display() == "Waiting for HR data" and not row.held_by_system
    row.status = RunEmployee.ON_HOLD
    row.save()
    assert row.held_by_system  # on hold with no person named: the cut-off did it
    with pytest.raises(IntegrityError), transaction.atomic():
        RunEmployee.objects.create(run=run, employee_code="BL016")
