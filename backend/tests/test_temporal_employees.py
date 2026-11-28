"""The month, employee and HR-sync workflows on a real Temporal server (the local dev server of the `temporal` CLI),
with short timers: two people missing a work location ask for it, one HR read covers both, HR is reminded, the
cut-off holds them, and the month is paid for everyone else with every person's workflow ending."""
import asyncio
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.conf import settings

from payroll.demo import setup
from payroll.features.hr_data import snapshots
from payroll.features.payroll_runs import runs
from payroll.models import DataSnapshot, PayrollRun, RunEmployee, RunEvent
from payroll_engine.hashing import sha256

TEMPORAL = shutil.which("temporal")
pytestmark = [pytest.mark.django_db(transaction=True),
              pytest.mark.skipif(not TEMPORAL, reason="the Temporal CLI is not installed (brew install temporal)")]


def _july_with_two_missing(company, snapshot):
    for m in ("2025-04", "2025-05", "2025-06"):
        setup.run_month(company, m, log=lambda *a: None)
    maker = setup.system_user("maker")
    codes = [e["code"] for e in snapshot.data["employees"][:2]]
    data = dict(snapshot.data)
    data["employees"] = [{k: v for k, v in e.items() if not (e["code"] in codes and k == "work_state")} for e in data["employees"]]
    data["incomplete"] = {c: ["work_state"] for c in codes}
    gap = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet", data=data,
                                      content_hash=sha256(snapshots.content_of(data)), checks=[])
    snapshots.approve(gap, maker)
    july = runs.open_month(company, "2025-07", maker, snapshot=gap)
    runs.review_data(july.pk, maker)
    return july, codes


async def _until(check, what, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await asyncio.to_thread(check):
            return
        await asyncio.sleep(0.3)
    raise AssertionError(f"timed out waiting for: {what}")


def test_people_waiting_for_hr_data_on_temporal(demo):
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from payroll.features.payroll_runs.workflow.activities import AGENT, ALL, hr_sync_id
    from payroll.features.payroll_runs.workflow.definitions import (AGENT_TASK_QUEUE, Command, EmployeeWorkflow, HRSyncWorkflow,
                                                                    PayrollMonthWorkflow, Timing)

    company, snapshot = demo
    july, codes = _july_with_two_missing(company, snapshot)
    maker, checker = setup.system_user("maker"), setup.system_user("checker")
    queue = settings.TEMPORAL_TASK_QUEUE

    async def main():
        env = await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL)
        try:
            client = env.client
            with ThreadPoolExecutor(max_workers=8) as pool, ThreadPoolExecutor(max_workers=1) as agent_pool:
                worker = Worker(client, task_queue=queue, workflows=[PayrollMonthWorkflow, EmployeeWorkflow, HRSyncWorkflow],
                                activities=ALL, activity_executor=pool)
                agent = Worker(client, task_queue=AGENT_TASK_QUEUE, activities=AGENT, activity_executor=agent_pool,
                               max_concurrent_activities=1)
                async with worker, agent:
                    timing = Timing(reminder_seconds=1, collect_seconds=1, reread_seconds=3600, cut_off_at=time.time() + 6)
                    wid = f"payroll-test-{uuid.uuid4().hex[:8]}"
                    month = await client.start_workflow(PayrollMonthWorkflow.run, args=[july.pk, timing], id=wid, task_queue=queue)

                    # Both ask; one HRSync reads once for both (no HR tool here, so it says so and stops).
                    sync = client.get_workflow_handle(hr_sync_id(company.code))
                    await _until(lambda: RunEvent.objects.filter(run=july, action="hr_sync_no_tool").exists(), "the HR read")
                    assert await sync.result() == "no_hr_tool"
                    reads = await sync.query(HRSyncWorkflow.state)
                    assert len(reads["reads"]) == 1 and reads["reads"][0]["people"] == sorted(f"{july.pk}:{c}" for c in codes)
                    event = await asyncio.to_thread(RunEvent.objects.get, run=july, action="hr_sync_no_tool")
                    assert sorted(event.detail["people"]) == sorted(codes) and "upload a spreadsheet" in event.message

                    # HR is reminded about each of them.
                    await _until(lambda: RunEvent.objects.filter(run=july, action="hr_reminder").count() == 2, "two reminders")

                    # The cut-off holds both; their workflows end on hold. Everyone else's waits for the month.
                    await _until(lambda: RunEmployee.objects.filter(run=july, status=RunEmployee.ON_HOLD).count() == 2, "the cut-off")
                    for c in codes:
                        assert await client.get_workflow_handle(f"{wid}-{c}").result() == "on_hold"
                    state = await month.query(PayrollMonthWorkflow.state)
                    people = await asyncio.to_thread(lambda: RunEmployee.objects.filter(run=july).count())
                    assert state["employees"] == people and set(state["employees_done"]) == set(codes)

                    # The month goes ahead for everyone else: calculate, submit, approve; then every person's workflow ends.
                    for name, who, status in (("calculate", maker, "calculated"), ("submit", maker, "awaiting_approval")):
                        await month.signal(PayrollMonthWorkflow.command, Command(name=name, actor_id=who.pk, request_id=name))
                        await _until(lambda s=status: PayrollRun.objects.get(pk=july.pk).status == s, name)
                    await month.signal(PayrollMonthWorkflow.command, Command(name="approve", actor_id=checker.pk, request_id="approve"))
                    assert await month.result() == "finalized"
                    other = await asyncio.to_thread(lambda: RunEmployee.objects.filter(run=july, status=RunEmployee.PAID)
                                                    .values_list("employee_code", flat=True).first())
                    assert other and await client.get_workflow_handle(f"{wid}-{other}").result() == "paid"
                    assert not await asyncio.to_thread(lambda: RunEmployee.objects.filter(
                        run=july, status__in=[RunEmployee.WAITING, RunEmployee.READY]).exists())
        finally:
            await env.shutdown()

    asyncio.run(main())


def test_data_arriving_releases_a_person_and_a_cancelled_month_ends_everyone(demo):
    from temporalio.client import WorkflowExecutionStatus
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Replayer, Worker

    from payroll.features.payroll_runs.workflow.activities import AGENT, ALL
    from payroll.features.payroll_runs.workflow.definitions import (AGENT_TASK_QUEUE, Command, EmployeeWorkflow, HRSyncWorkflow,
                                                                    PayrollMonthWorkflow, Timing)

    company, snapshot = demo
    july, codes = _july_with_two_missing(company, snapshot)
    maker, checker = setup.system_user("maker"), setup.system_user("checker")
    queue = settings.TEMPORAL_TASK_QUEUE
    complete = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet",
                                           data=dict(snapshot.data), content_hash="complete-1", checks=[])
    snapshots.approve(complete, maker)

    async def main():
        env = await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL)
        try:
            client = env.client
            with ThreadPoolExecutor(max_workers=4) as pool, ThreadPoolExecutor(max_workers=1) as agent_pool:
                worker = Worker(client, task_queue=queue, workflows=[PayrollMonthWorkflow, EmployeeWorkflow, HRSyncWorkflow],
                                activities=ALL, activity_executor=pool)
                agent = Worker(client, task_queue=AGENT_TASK_QUEUE, activities=AGENT, activity_executor=agent_pool,
                               max_concurrent_activities=1)
                async with worker, agent:
                    far = Timing(reminder_seconds=3600, collect_seconds=1, reread_seconds=3600, cut_off_at=time.time() + 3600)
                    wid = f"payroll-test-{uuid.uuid4().hex[:8]}"
                    month = await client.start_workflow(PayrollMonthWorkflow.run, args=[july.pk, far], id=wid, task_queue=queue)
                    first = client.get_workflow_handle(f"{wid}-{codes[0]}")
                    await _until(lambda: RunEvent.objects.filter(run=july, action="hr_sync_no_tool").exists(), "the HR read")
                    assert (await first.query(EmployeeWorkflow.state))["status"] == "waiting"

                    # HR adds the work locations; the maker uses the new data; the month tells everyone to check again.
                    await asyncio.to_thread(runs.use_snapshot, july.pk, complete, maker)
                    await month.signal(PayrollMonthWorkflow.data_changed)

                    async def ready():
                        return (await first.query(EmployeeWorkflow.state))["status"] == "ready"
                    for _ in range(100):
                        if await ready():
                            break
                        await asyncio.sleep(0.2)
                    assert await ready()  # released: no longer waiting, paid with everyone

                    await asyncio.to_thread(runs.review_data, july.pk, maker)
                    for name, who, status in (("calculate", maker, "calculated"), ("submit", maker, "awaiting_approval")):
                        await month.signal(PayrollMonthWorkflow.command, Command(name=name, actor_id=who.pk, request_id=name))
                        await _until(lambda s=status: PayrollRun.objects.get(pk=july.pk).status == s, name)
                    await month.signal(PayrollMonthWorkflow.command, Command(name="approve", actor_id=checker.pk, request_id="approve"))
                    assert await month.result() == "finalized"
                    assert await first.result() == "paid"  # the person who waited is paid in July itself

                    # The recorded histories replay with the current code: no non-determinism in any of the three workflows.
                    replayer = Replayer(workflows=[PayrollMonthWorkflow, EmployeeWorkflow, HRSyncWorkflow])
                    for handle in (month, first, client.get_workflow_handle(f"hr-sync-{company.code}")):
                        await replayer.replay_workflow(await handle.fetch_history())

                    # A cancelled month ends every person's workflow with it.
                    await asyncio.to_thread(_read_again_and_open_august, company, maker)
                    august = await asyncio.to_thread(lambda: PayrollRun.objects.get(company=company, period="2025-08", run_type="regular"))
                    wid2 = f"payroll-test-{uuid.uuid4().hex[:8]}"
                    month2 = await client.start_workflow(PayrollMonthWorkflow.run, args=[august.pk, far], id=wid2, task_queue=queue)
                    someone = await asyncio.to_thread(lambda: august.employee_states.values_list("employee_code", flat=True).first())
                    child = client.get_workflow_handle(f"{wid2}-{someone}")
                    await _until_async(lambda: child.describe(), "the person's workflow to start")
                    await month2.signal(PayrollMonthWorkflow.command, Command(name="cancel", actor_id=maker.pk, request_id="cancel"))
                    assert await month2.result() == "cancelled"
                    for _ in range(50):
                        if (await child.describe()).status == WorkflowExecutionStatus.TERMINATED:
                            break
                        await asyncio.sleep(0.2)
                    assert (await child.describe()).status == WorkflowExecutionStatus.TERMINATED
        finally:
            await env.shutdown()

    asyncio.run(main())


def _read_again_and_open_august(company, maker):
    s = DataSnapshot.objects.filter(company=company, status="approved").order_by("-id").first()
    newer = DataSnapshot.objects.create(company=company, source_kind="spreadsheet", source_system="Spreadsheet", data=dict(s.data),
                                        content_hash="complete-2", checks=[])
    snapshots.approve(newer, maker)
    runs.open_month(company, "2025-08", maker, snapshot=newer, require_fresh_data=True)


async def _until_async(probe, what, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            await probe()
            return
        except Exception:  # noqa: BLE001 - not there yet
            await asyncio.sleep(0.3)
    raise AssertionError(f"timed out waiting for: {what}")
