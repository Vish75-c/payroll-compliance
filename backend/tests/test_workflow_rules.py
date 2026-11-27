"""Maker/checker, repeatability, stale data, finalise-once, and rule changes as data edits."""
from datetime import date

import pytest

from payroll.models import PayrollRun
from payroll_engine.periods import Period
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
    assert run.employee_states.count() > 0  # opening the month made a row for everyone in it
    row = run.employee_states.get(employee_code="BL016")
    row.status, row.missing = RunEmployee.WAITING, ["work_state"]
    row.save()
    assert row.get_status_display() == "Waiting for HR data" and not row.held_by_system
    row.status = RunEmployee.ON_HOLD
    row.save()
    assert row.held_by_system  # on hold with no person named: the cut-off did it
    with pytest.raises(IntegrityError), transaction.atomic():
        RunEmployee.objects.create(run=run, employee_code="BL016")


def test_a_person_missing_hr_data_is_left_out_and_everyone_else_is_calculated(demo):
    from payroll.models import DataSnapshot
    from payroll_engine.hashing import sha256

    company, snapshot = demo
    maker = setup.system_user("maker")
    data = dict(snapshot.data)
    first, second = data["employees"][0]["code"], data["employees"][1]["code"]
    data["employees"] = [{k: v for k, v in e.items() if not (e["code"] == first and k == "work_state")} for e in data["employees"]]
    data["incomplete"] = {first: ["work_state"]}
    gap = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet", data=data,
                                      content_hash=sha256(snapshots.content_of(data)),
                                      checks=[{"level": "warning", "code": "no_work_state", "message": "1 of n"}])
    snapshots.approve(gap, maker)  # a warning, not an error: the data can be used
    run = runs.open_month(company, "2025-04", maker, snapshot=gap)
    runs.review_data(run.pk, maker)
    run = runs.calculate(run.pk, maker)
    paid = {e["code"] for e in run.result["employees"]}
    assert first not in paid and second in paid
    assert run.result["employee_count"] == len(paid)
    assert first not in {e["code"] for e in run.input["employees"]}  # not in the input either: nothing is guessed


def test_someone_on_hold_stops_the_next_month_until_their_hr_data_arrives(demo):
    from payroll.models import DataSnapshot, RunEmployee
    from payroll_engine.hashing import sha256

    company, snapshot = demo
    history(company)  # April to June, final
    maker = setup.system_user("maker")
    june = PayrollRun.objects.get(company=company, period="2025-06", run_type="regular")
    code = snapshot.data["employees"][0]["code"]
    assert june.employee_states.get(employee_code=code).status == RunEmployee.PAID  # finalising marked everyone paid
    june.employee_states.filter(employee_code=code).update(status=RunEmployee.ON_HOLD, missing=["work_state"])

    def reviewed(data):
        s = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet", data=data,
                                        content_hash=sha256(snapshots.content_of(data)), checks=[])
        return snapshots.approve(s, maker)

    # HR has not added the work state yet: July cannot start.
    reviewed(dict(snapshot.data, incomplete={code: ["work_state"]}))
    with pytest.raises(RuleViolation, match=f"once everyone on hold has their HR data: {code} \\(work state, on hold since June 2025\\)") as exc:
        runs.open_month(company, "2025-07", maker)
    assert exc.value.code == "previous_not_done"

    # HR adds it and the new data is reviewed: July can start (and pays June's held salary).
    reviewed(dict(snapshot.data))
    assert runs.previous_month_block(company, Period.parse("2025-07")) == ""
    assert runs.open_month(company, "2025-07", maker).period == "2025-07"


def test_a_held_salary_already_paid_does_not_block(demo):
    from payroll.models import RunEmployee

    company, _ = demo
    history(company)
    june = PayrollRun.objects.get(company=company, period="2025-06", run_type="regular")
    may = PayrollRun.objects.get(company=company, period="2025-05", run_type="regular")
    RunEmployee.objects.create(run=may, employee_code="NOT-IN-DATA", status=RunEmployee.PAID_LATER, paid_in_run=june)
    assert runs.previous_month_block(company, Period.parse("2025-07")) == ""


def _with_gap(company, snapshot, maker, code, field="work_state"):
    from payroll.models import DataSnapshot
    from payroll_engine.hashing import sha256

    data = dict(snapshot.data)
    data["employees"] = [{k: v for k, v in e.items() if not (e["code"] == code and k == field)} for e in data["employees"]]
    data["incomplete"] = {code: [field]}
    s = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet", data=data,
                                    content_hash=sha256(snapshots.content_of(data)), checks=[])
    return snapshots.approve(s, maker)


def test_waiting_people_block_submit_until_held_and_are_marked_paid_or_on_hold(demo):
    from payroll.features.payroll_runs import employees
    from payroll.models import RunEmployee

    company, snapshot = demo
    history(company)
    maker, checker = setup.system_user("maker"), setup.system_user("checker")
    code = snapshot.data["employees"][0]["code"]
    gap = _with_gap(company, snapshot, maker, code)
    july = runs.open_month(company, "2025-07", maker, snapshot=gap)
    state = {r.employee_code: r for r in july.employee_states.all()}
    assert state[code].status == RunEmployee.WAITING and state[code].missing == ["work_state"]
    assert all(r.status == RunEmployee.READY for c, r in state.items() if c != code)

    runs.review_data(july.pk, maker)
    july = runs.calculate(july.pk, maker)
    assert code not in {e["code"] for e in july.result["employees"]}
    with pytest.raises(RuleViolation, match=f"1 person is waiting for HR data \\({code}: work state\\)") as exc:
        runs.submit(july.pk, maker)
    assert exc.value.code == "people_waiting"

    with pytest.raises(RuleViolation, match="Say why"):
        employees.hold(july.pk, code, maker, " ")
    with pytest.raises(RuleViolation):
        employees.hold(july.pk, code, checker, "x")  # only the finance team employee holds people
    other = next(c for c in state if c != code)
    with pytest.raises(RuleViolation, match="complete data"):
        employees.hold(july.pk, other, maker, "x")
    employees.hold(july.pk, code, maker, "HR is confirming the work location")

    runs.submit(july.pk, maker)
    with pytest.raises(RuleViolation, match="on hold"):
        employees.hold(july.pk, other, maker, "x")  # submitted: nothing changes now
    runs.approve(july.pk, checker)
    runs.finalize(july.pk, checker)
    state = {r.employee_code: r.status for r in july.employee_states.all()}
    assert state[code] == RunEmployee.ON_HOLD and state[other] == RunEmployee.PAID
    assert runs.people_on_hold_without_data(company, Period.parse("2025-08"))  # August waits for the data


def test_data_arriving_before_submit_takes_a_person_off_hold(demo):
    from payroll.features.payroll_runs import employees
    from payroll.models import DataSnapshot, RunEmployee, RunEvent

    company, snapshot = demo
    history(company)
    maker = setup.system_user("maker")
    code = snapshot.data["employees"][0]["code"]
    july = runs.open_month(company, "2025-07", maker, snapshot=_with_gap(company, snapshot, maker, code))
    employees.hold(july.pk, code, maker, "waiting for HR")
    complete = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet",
                                           data=dict(snapshot.data), content_hash=snapshot.content_hash + "x", checks=[])
    snapshots.approve(complete, maker)
    runs.use_snapshot(july.pk, complete, maker)
    row = july.employee_states.get(employee_code=code)
    assert row.status == RunEmployee.READY and row.held_at is None
    assert RunEvent.objects.filter(run=july, action="hold_released").exists()
    runs.review_data(july.pk, maker)
    assert code in {e["code"] for e in runs.calculate(july.pk, maker).result["employees"]}


def test_the_cut_off_holds_everyone_still_waiting(demo):
    from datetime import date as _date

    from payroll.features.payroll_runs import employees
    from payroll.models import RunEmployee

    company, snapshot = demo
    history(company)
    maker = setup.system_user("maker")
    code = snapshot.data["employees"][0]["code"]
    july = runs.open_month(company, "2025-07", maker, snapshot=_with_gap(company, snapshot, maker, code))
    assert employees.hold_waiting_at_cut_off(july.pk, today=_date(2025, 7, 27)) == []  # before the 28th: keep waiting
    held = employees.hold_waiting_at_cut_off(july.pk, today=_date(2025, 7, 28))
    assert [h.employee_code for h in held] == [code]
    row = july.employee_states.get(employee_code=code)
    assert row.status == RunEmployee.ON_HOLD and row.held_by_system and "cut-off (28 Jul 2025)" in row.hold_reason
