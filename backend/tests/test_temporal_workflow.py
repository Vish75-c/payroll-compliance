"""The month's Temporal workflow, run on a real Temporal server (the SDK downloads one for tests).

The activities here are stand-ins with the same names as the real ones, so the test checks
the workflow itself: it waits for people without a busy process, keeps waiting when the
worker is stopped and replaced (what a restart of every service looks like to a workflow),
ignores a button press delivered twice, survives a refused step, and finalises exactly once.

Skipped when the test server cannot be started (for example, no network on first use).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from payroll.features.payroll_runs.workflow.definitions import Command, PayrollMonthWorkflow

QUEUE = "payroll-months-test"


class FakeSteps:
    """Records every step the workflow runs; the checker refuses the first approval."""

    def __init__(self, refuse_first_approve: bool = False, fail_first_finalize: bool = False):
        self.calls: list[str] = []
        self.refuse_first_approve = refuse_first_approve
        self.fail_first_finalize = fail_first_finalize

    def activities(self):
        def step(name: str, status: str):
            @activity.defn(name=f"payroll.{name}")
            async def run(run_id: int, actor_id: int, note: str) -> str:
                self.calls.append(name)
                if name == "approve" and self.refuse_first_approve:
                    self.refuse_first_approve = False
                    raise ApplicationError("The maker cannot approve their own month.", type="RuleViolation", non_retryable=True)
                if name == "finalize" and self.fail_first_finalize:
                    self.fail_first_finalize = False
                    raise ApplicationError("Payment files do not match the register: TDS", type="RuleViolation", non_retryable=True)
                return status
            return run

        @activity.defn(name="payroll.month_people")
        async def month_people(run_id: int) -> dict:
            # No people: these tests are about the month's own steps (the people's workflows have their own test).
            return {"company_code": "TEST", "codes": [], "cut_off_at": 4102444800.0}  # cut-off in 2100: never reached here

        return [step("calculate", "calculated"), step("submit", "submitted"), step("approve", "approved"),
                step("reject", "rejected"), step("cancel", "cancelled"), step("finalize", "final"), month_people]


def cmd(name: str, actor: int = 1, request_id: str | None = None) -> Command:
    return Command(name=name, actor_id=actor, note="", request_id=request_id or uuid.uuid4().hex)


async def wait_for(handle, predicate, timeout: float = 20.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        state = await handle.query(PayrollMonthWorkflow.state)
        if predicate(state):
            return state
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"workflow state never matched; last state {state}")
        await asyncio.sleep(0.1)


def run_async(coro_factory):
    async def go():
        try:
            env = await WorkflowEnvironment.start_local()
        except Exception as exc:  # noqa: BLE001 - no server binary available here
            pytest.skip(f"Temporal test server could not start: {exc}")
        async with env:
            await coro_factory(env)

    asyncio.run(go())


def worker(env, steps: FakeSteps) -> Worker:
    return Worker(env.client, task_queue=QUEUE, workflows=[PayrollMonthWorkflow], activities=steps.activities())


def test_month_keeps_waiting_when_the_worker_is_replaced_and_finalises_once():
    steps = FakeSteps()

    async def scenario(env):
        wid = f"payroll-test-{uuid.uuid4().hex[:8]}"
        async with worker(env, steps):
            handle = await env.client.start_workflow(PayrollMonthWorkflow.run, 7, id=wid, task_queue=QUEUE)
            await handle.signal(PayrollMonthWorkflow.command, cmd("calculate"))
            await handle.signal(PayrollMonthWorkflow.command, cmd("submit"))
            state = await wait_for(handle, lambda s: s["status"] == "submitted")
            assert state["pending_commands"] == 0

        # No worker at all: the month is still running in Temporal, waiting for the checker.
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
        press = uuid.uuid4().hex
        await handle.signal(PayrollMonthWorkflow.command, cmd("approve", actor=2, request_id=press))
        await handle.signal(PayrollMonthWorkflow.command, cmd("approve", actor=2, request_id=press))  # same press twice

        # A brand-new worker (new process after a restart) picks the month up where it was.
        async with worker(env, steps):
            assert await handle.result() == "finalized"

        assert steps.calls == ["calculate", "submit", "approve", "finalize"]

        # The recorded history replays with the current workflow code: no non-determinism.
        history = await handle.fetch_history()
        await Replayer(workflows=[PayrollMonthWorkflow]).replay_workflow(history)

    run_async(scenario)


def test_refused_step_keeps_the_month_waiting():
    steps = FakeSteps(refuse_first_approve=True)

    async def scenario(env):
        async with worker(env, steps):
            handle = await env.client.start_workflow(PayrollMonthWorkflow.run, 8, id=f"payroll-test-{uuid.uuid4().hex[:8]}",
                                                     task_queue=QUEUE, execution_timeout=timedelta(minutes=2))
            for name in ("calculate", "submit", "approve"):
                await handle.signal(PayrollMonthWorkflow.command, cmd(name))
            state = await wait_for(handle, lambda s: s["last_error"] != "")
            assert "cannot approve their own month" in state["last_error"]
            assert state["status"] == "submitted"
            assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING

            await handle.signal(PayrollMonthWorkflow.command, cmd("approve", actor=2))
            assert await handle.result() == "finalized"
        assert steps.calls == ["calculate", "submit", "approve", "approve", "finalize"]

    run_async(scenario)


def test_failed_finalise_keeps_the_month_approved_and_waiting():
    steps = FakeSteps(fail_first_finalize=True)

    async def scenario(env):
        async with worker(env, steps):
            handle = await env.client.start_workflow(PayrollMonthWorkflow.run, 9, id=f"payroll-test-{uuid.uuid4().hex[:8]}",
                                                     task_queue=QUEUE, execution_timeout=timedelta(minutes=2))
            for name in ("calculate", "submit"):
                await handle.signal(PayrollMonthWorkflow.command, cmd(name))
            await handle.signal(PayrollMonthWorkflow.command, cmd("approve", actor=2))
            state = await wait_for(handle, lambda s: s["last_error"] != "")
            assert "do not match the register" in state["last_error"]
            assert state["status"] == "approved"  # the approval stands; the workflow did not fail
            assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING

            await handle.signal(PayrollMonthWorkflow.command, cmd("finalize", actor=2))
            assert await handle.result() == "finalized"
        assert steps.calls == ["calculate", "submit", "approve", "finalize", "finalize"]

    run_async(scenario)
