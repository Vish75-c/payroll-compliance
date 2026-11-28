"""More of the on-hold work: the agent reading the HR tool again for the people waiting (with a stand-in for the
browser, so no HR tool has to run), the leave check for a held month, the cut-off command for local mode, and what
the screens are told."""
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from payroll.demo import setup
from payroll.features.hr_data import jobs, sync
from payroll.features.hr_data.present import snapshot_summary
from payroll.features.payroll_runs import employees, runs
from payroll.features.payroll_runs import present as P
from payroll.features.payroll_runs.summary import run_summary
from payroll.models import Connector, DataSnapshot, PayrollRun, RunEmployee, RunEvent

from .test_workflow_rules import _complete_data, _hold_in_june, _with_gap, emp

pytestmark = pytest.mark.django_db
PERSON = "BL001"


# --------------------------------------------------------------------------- the agent reads again

def _hr_tool(company, snapshot):
    """An HR tool set up and confirmed, named like the demo's data so its confirmed field mappings are reused."""
    return Connector.objects.create(company=company, name="HR tool", kind=Connector.KIND_BROWSER, system_name=snapshot.job.source_system,
                                    recipe={"datasets": {"employees": {"url": "http://hr.example/employees"}}},
                                    recipe_confirmed_at=timezone.now())


def _agent_reads(monkeypatch, snapshot, blank_location_for=(), fail=False):
    """Stand-in for the browser agent: 'reads' the demo's tables (optionally with someone's work location still blank)."""
    calls = []

    def read(job_id):
        calls.append(job_id)
        job = jobs.IngestionJob.objects.get(pk=job_id)
        if fail:
            job.status, job.error = "failed", "the HR tool did not answer"
            job.save()
            return
        tables = []
        for t in snapshot.job.tables.all():
            rows = [dict(r, **{"Work Location": None}) if r.get("Employee ID") in blank_location_for and "Work Location" in r else r
                    for r in t.rows]
            tables.append({"name": t.name, "headers": t.headers, "rows": rows, "dataset_hint": t.dataset_hint})
        job.status, job.finished_at = "needs_mapping", timezone.now()
        job.save()
        jobs.store_tables(job, tables)

    monkeypatch.setattr(jobs, "_run_browser_job", read)
    return calls


def _june_waiting(company, snapshot):
    for m in ("2025-04", "2025-05"):
        setup.run_month(company, m, log=lambda *a: None)
    maker = setup.system_user("maker")
    return runs.open_month(company, "2025-06", maker, snapshot=_with_gap(company, snapshot, maker, PERSON))


def test_one_read_finds_the_missing_data_and_waits_for_review(demo, monkeypatch):
    company, snapshot = demo
    june = _june_waiting(company, snapshot)
    _hr_tool(company, snapshot)
    calls = _agent_reads(monkeypatch, snapshot)
    out = sync.read_for_waiting(company.code, [{"run_id": june.pk, "code": PERSON, "missing": ["work_state"]}])
    assert len(calls) == 1 and out["status"] == "read"
    assert out["found"] == [f"{june.pk}:{PERSON}"] and out["still_missing"] == [] and out["stop"] is False
    new = DataSnapshot.objects.get(pk=out["snapshot_id"])
    assert new.status == "pending_review"  # nothing is used for pay until a person reviews it
    assert snapshot_summary(new)["read_for_waiting"] == [PERSON] and snapshot_summary(new)["incomplete"] == {}
    event = RunEvent.objects.get(run=june, action="hr_sync_read")
    assert f"Found: {PERSON}" in event.message and "Review the new HR data" in event.message
    assert june.employee_states.get(employee_code=PERSON).status == RunEmployee.WAITING  # still, until the maker switches


def test_a_read_that_still_lacks_the_data_says_so_and_is_tried_again(demo, monkeypatch):
    company, snapshot = demo
    june = _june_waiting(company, snapshot)
    _hr_tool(company, snapshot)
    _agent_reads(monkeypatch, snapshot, blank_location_for=[PERSON])
    out = sync.read_for_waiting(company.code, [{"run_id": june.pk, "code": PERSON, "missing": ["work_state"]}])
    assert out["status"] == "read" and out["found"] == [] and out["still_missing"] == [f"{june.pk}:{PERSON}"] and not out["stop"]
    assert "Still missing: BL001 (work state). The agent reads again later." in RunEvent.objects.get(run=june, action="hr_sync_read").message


def test_a_failed_read_is_retried_and_nobody_waiting_means_no_read(demo, monkeypatch):
    company, snapshot = demo
    june = _june_waiting(company, snapshot)
    _hr_tool(company, snapshot)
    calls = _agent_reads(monkeypatch, snapshot, fail=True)
    with pytest.raises(RuntimeError, match="did not answer"):  # the workflow's retry policy tries again
        sync.read_for_waiting(company.code, [{"run_id": june.pk, "code": PERSON, "missing": ["work_state"]}])
    employees.hold(june.pk, PERSON, setup.system_user("maker"), "HR is on leave")
    out = sync.read_for_waiting(company.code, [{"run_id": june.pk, "code": PERSON, "missing": ["work_state"]}])
    assert out["status"] == "nobody_waiting" and len(calls) == 1  # held already: no second read


def test_without_an_hr_tool_the_maker_is_told_to_upload(demo):
    company, snapshot = demo
    june = _june_waiting(company, snapshot)
    out = sync.read_for_waiting(company.code, [{"run_id": june.pk, "code": PERSON, "missing": ["work_state"]}])
    assert out["status"] == "no_hr_tool" and out["stop"]
    assert "upload a spreadsheet" in RunEvent.objects.get(run=june, action="hr_sync_no_tool").message


# --------------------------------------------------------------------------- paying a held month

def test_leave_read_only_after_the_held_month_is_flagged(demo):
    company, snapshot = demo
    maker = setup.system_user("maker")
    _hold_in_june(company, snapshot, PERSON)
    data = dict(snapshot.data)
    data["leave"] = [r for r in data["leave"] if r["start_date"] >= "2025-07-01"]
    assert data["leave"], "the demo has leave in July or later"
    july = runs.open_month(company, "2025-07", maker, snapshot=_complete_data(company, snapshot, maker, data))
    runs.review_data(july.pk, maker)
    july = runs.calculate(july.pk, maker)
    notes = [i["message"] for i in emp(july, PERSON)["issues"]]
    assert any("leave in the HR data starts after June 2025" in n and "Read leave from 01 Jun 2025" in n for n in notes)


# --------------------------------------------------------------------------- local mode: the cut-off command

def test_the_cut_off_command_holds_waiting_people_in_open_months(demo):
    company, snapshot = demo
    june = _june_waiting(company, snapshot)  # June 2025's cut-off (28 Jun 2025) has long passed
    call_command("hold_at_cut_off")
    row = june.employee_states.get(employee_code=PERSON)
    assert row.status == RunEmployee.ON_HOLD and row.held_by_system
    call_command("hold_at_cut_off")  # running it again changes nothing
    assert RunEvent.objects.filter(run=june, action="held_at_cut_off").count() == 1


def test_the_cut_off_command_leaves_a_month_before_its_cut_off_alone(demo, monkeypatch):
    company, snapshot = demo
    june = _june_waiting(company, snapshot)
    monkeypatch.setattr(timezone, "localdate", lambda: june.period_obj.start + timedelta(days=5))
    call_command("hold_at_cut_off")
    assert june.employee_states.get(employee_code=PERSON).status == RunEmployee.WAITING


# --------------------------------------------------------------------------- what the screens are told

def test_the_screens_are_told_who_is_on_hold_and_who_is_back(demo):
    company, snapshot = demo
    maker = setup.system_user("maker")
    june = _hold_in_june(company, snapshot, PERSON)
    assert run_summary(june) | {"people_on_hold": 1, "people_waiting": 0} == run_summary(june)  # the Overview's counts
    june_page = P.run_detail(june, maker, with_workflow=False)
    held = next(e for e in june_page["comparison"]["employees"] if e["code"] == PERSON)
    assert held["drivers"] == [{"kind": "held", "text": "On hold (work state missing): not paid in June 2025"}]

    july = runs.open_month(company, "2025-07", maker, snapshot=_complete_data(company, snapshot, maker))
    runs.review_data(july.pk, maker)
    july = runs.calculate(july.pk, maker)
    back = next(e for e in P.run_detail(july, maker, with_workflow=False)["comparison"]["employees"] if e["code"] == PERSON)
    assert back["drivers"][0]["kind"] == "back_from_hold" and "plus the salary held in June 2025" in back["drivers"][0]["text"]
    assert run_summary(july)["people_on_hold"] == 0
    assert PayrollRun.objects.get(pk=june.pk).employee_states.get(employee_code=PERSON).status == RunEmployee.ON_HOLD  # until July is final
