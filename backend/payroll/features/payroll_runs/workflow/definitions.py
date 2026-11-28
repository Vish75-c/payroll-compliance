"""Temporal workflows for a payroll month, its employees, and reading the HR tool. Orchestration only: no Django,
no I/O, deterministic.

PayrollMonth waits for commands (signals) from people: calculate, submit, approve, reject, cancel. It starts one
PayrollEmployee child per person in the month and runs the month's cut-off timer. A child follows one person: if
their HR data is missing a fact, it asks the company's HRSync for it, reminds HR after a while, and ends once the
person is paid or on hold. HRSync collects every request and reads the HR tool once for all of them.

Waiting costs nothing: no process is busy, and the waiting state lives in Temporal's database, so it survives
restarts of the API, the worker and Temporal itself. Every step runs as an activity that re-checks the rules in the
payroll database, which stays the record of who is waiting, ready, paid or on hold.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.workflow import ParentClosePolicy

TASK_QUEUE_DEFAULT = "payroll-months"
AGENT_TASK_QUEUE = "hrms-agent"  # the agent reads the HR tool here, one read at a time
EMPLOYEE_DONE = ("paid", "on_hold", "paid_later", "gone")

QUICK = RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0, maximum_interval=timedelta(minutes=1),
                    maximum_attempts=10, non_retryable_error_types=["RuleViolation"])
READ_RETRY = RetryPolicy(initial_interval=timedelta(minutes=1), backoff_coefficient=2.0, maximum_interval=timedelta(minutes=30),
                         maximum_attempts=5, non_retryable_error_types=["RuleViolation"])


@dataclass
class Timing:
    """How long the workflows wait. The defaults are the real ones; tests pass shorter ones."""
    reminder_seconds: int = 2 * 24 * 3600   # remind HR when a person's data is still missing after this long
    collect_seconds: int = 120              # HRSync waits this long for more requests before one read
    reread_seconds: int = 6 * 3600          # read the HR tool again for anyone still missing
    cut_off_at: float | None = None         # epoch seconds; None: the company's cut-off day for the month


@dataclass
class SyncRequest:
    run_id: int
    code: str
    missing: list[str] = field(default_factory=list)

STEP_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=10,
    non_retryable_error_types=["RuleViolation"],
)
FINALIZE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=20,
    non_retryable_error_types=["RuleViolation"],
)


@dataclass
class Command:
    name: str  # calculate | submit | approve | reject | cancel | finalize (retry after a failed finalise)
    actor_id: int
    note: str = ""
    request_id: str = ""


@dataclass
class MonthState:
    run_id: int
    status: str = "draft"
    handled: list[str] = field(default_factory=list)
    last_error: str = ""


@workflow.defn(name="PayrollMonth")
class PayrollMonthWorkflow:
    def __init__(self) -> None:
        self._queue: list[Command] = []
        self._state: MonthState | None = None
        self._recheck = False
        self._children: dict[str, workflow.ChildWorkflowHandle] = {}
        self._finished: dict[str, str] = {}  # code -> how the person's workflow ended

    @workflow.signal(name="command")
    def command(self, cmd: Command) -> None:
        self._queue.append(cmd)

    @workflow.signal(name="data_changed")
    def data_changed(self) -> None:
        """The month's HR data was switched, or someone was put on hold: every person checks their state again."""
        self._recheck = True

    @workflow.query(name="state")
    def state(self) -> dict:
        s = self._state
        return {"status": s.status if s else "starting", "pending_commands": len(self._queue),
                "last_error": s.last_error if s else "", "handled": len(s.handled) if s else 0,
                "employees": len(self._children), "employees_done": dict(self._finished)}

    @workflow.run
    async def run(self, run_id: int, timing: Timing | None = None) -> str:
        timing = timing or Timing()
        self._state = MonthState(run_id=run_id)
        month = await workflow.execute_activity("payroll.month_people", args=[run_id], start_to_close_timeout=timedelta(minutes=2),
                                                retry_policy=QUICK)
        me = workflow.info().workflow_id
        for code in month["codes"]:
            handle = await workflow.start_child_workflow(
                EmployeeWorkflow.run, args=[run_id, code, month["company_code"], timing], id=f"{me}-{code}",
                parent_close_policy=ParentClosePolicy.TERMINATE)
            self._children[code] = handle
            asyncio.create_task(self._watch(code, handle))
        cut_off = timing.cut_off_at if timing.cut_off_at is not None else month["cut_off_at"]
        asyncio.create_task(self._cut_off_timer(cut_off))
        while True:
            await workflow.wait_condition(lambda: len(self._queue) > 0 or self._recheck)
            if self._recheck:
                self._recheck = False
                await self._tell_everyone()
                continue
            cmd = self._queue.pop(0)
            if cmd.request_id and cmd.request_id in self._state.handled:
                continue  # the same button press delivered twice
            try:
                result = await workflow.execute_activity(
                    f"payroll.{cmd.name}",
                    args=[run_id, cmd.actor_id, cmd.note],
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=STEP_RETRY,
                )
                self._state.status = result
                self._state.last_error = ""
            except ActivityError as err:
                cause = err.cause
                self._state.last_error = cause.message if isinstance(cause, ApplicationError) else str(err)
                if cmd.request_id:
                    self._state.handled.append(cmd.request_id)
                continue
            if cmd.request_id:
                self._state.handled.append(cmd.request_id)
            if cmd.name == "cut_off":
                await self._tell_everyone()  # the people just held end their workflows
            if cmd.name == "approve":
                # Approved: finalise and write the files. Finalising twice is a no-op. If it still fails after
                # its retries, the month stays approved and the workflow keeps waiting for a "finalize" command.
                if await self._finalize(run_id, cmd.actor_id):
                    return await self._close("finalized")
            if cmd.name == "finalize":  # a retry after a failed finalise, and it succeeded
                return await self._close("finalized")
            if cmd.name == "cancel":
                return "cancelled"  # the people's workflows are ended with the month
            if workflow.info().get_current_history_length() > 5000 and not self._children:
                workflow.continue_as_new(args=[run_id, timing])

    async def _watch(self, code: str, handle) -> None:
        try:
            self._finished[code] = await handle
        except Exception:  # noqa: BLE001 - ended with the month (cancelled) or failed: shown as such
            self._finished[code] = "ended"

    async def _cut_off_timer(self, at: float) -> None:
        delay = at - workflow.now().timestamp()
        if delay > 0:
            await asyncio.sleep(delay)
        self._queue.append(Command(name="cut_off", actor_id=0, request_id="cut-off"))

    async def _tell_everyone(self) -> None:
        for code, handle in self._children.items():
            if code in self._finished:
                continue
            try:
                await handle.signal(EmployeeWorkflow.recheck)
            except Exception:  # noqa: BLE001 - it ended in the meantime
                pass

    async def _close(self, outcome: str) -> str:
        """The month is final: each person's workflow sees paid or on hold and ends; wait for all of them."""
        await self._tell_everyone()
        await workflow.wait_condition(lambda: len(self._finished) == len(self._children))
        return outcome

    async def _finalize(self, run_id: int, actor_id: int) -> bool:
        try:
            self._state.status = await workflow.execute_activity(
                "payroll.finalize",
                args=[run_id, actor_id, ""],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=FINALIZE_RETRY,
            )
            self._state.last_error = ""
            return True
        except ActivityError as err:
            cause = err.cause
            self._state.status = "approved"
            self._state.last_error = cause.message if isinstance(cause, ApplicationError) else str(err)
            return False


@workflow.defn(name="PayrollEmployee")
class EmployeeWorkflow:
    """One person in one month. The payroll database says where they stand; this waits, asks and reminds."""

    def __init__(self) -> None:
        self._recheck = False
        self._state: dict = {}
        self._asked = False
        self._reminded = False

    @workflow.signal(name="recheck")
    def recheck(self) -> None:
        self._recheck = True

    @workflow.query(name="state")
    def state(self) -> dict:
        return {**self._state, "asked_hr_tool": self._asked, "reminded_hr": self._reminded}

    @workflow.run
    async def run(self, run_id: int, code: str, company_code: str, timing: Timing | None = None) -> str:
        timing = timing or Timing()
        while True:
            self._recheck = False
            self._state = await workflow.execute_activity("payroll.employee_state", args=[run_id, code],
                                                          start_to_close_timeout=timedelta(minutes=1), retry_policy=QUICK)
            status = self._state["status"]
            if status in EMPLOYEE_DONE:
                return status
            if status == "waiting":
                if not self._asked:
                    await workflow.execute_activity("hrsync.request", args=[company_code, SyncRequest(run_id, code, self._state["missing"]), timing],
                                                    start_to_close_timeout=timedelta(minutes=1), retry_policy=QUICK)
                    self._asked = True
                if not self._reminded:
                    try:
                        await workflow.wait_condition(lambda: self._recheck, timeout=timedelta(seconds=timing.reminder_seconds))
                    except asyncio.TimeoutError:
                        await workflow.execute_activity("payroll.remind_hr", args=[run_id, code, self._state["missing"]],
                                                        start_to_close_timeout=timedelta(minutes=1), retry_policy=QUICK)
                        self._reminded = True
                    continue
            else:
                self._asked = False  # ready: if a fact goes missing again later, ask again
            await workflow.wait_condition(lambda: self._recheck)


@workflow.defn(name="HRSync")
class HRSyncWorkflow:
    """One per company: collects the people waiting for HR data and reads the HR tool once for all of them."""

    def __init__(self) -> None:
        self._pending: dict[str, SyncRequest] = {}
        self._reads: list[dict] = []

    @workflow.signal(name="need")
    def need(self, req: SyncRequest) -> None:
        self._pending[f"{req.run_id}:{req.code}"] = req

    @workflow.query(name="state")
    def state(self) -> dict:
        return {"waiting_to_read": sorted(self._pending), "reads": self._reads}

    @workflow.run
    async def run(self, company_code: str, timing: Timing | None = None) -> str:
        timing = timing or Timing()
        again: dict[str, SyncRequest] = {}  # still missing after the last read
        while True:
            if not self._pending and not again:
                return f"done after {len(self._reads)} read(s)"
            if not self._pending:
                try:  # read again later for those still missing, or sooner when someone new asks
                    await workflow.wait_condition(lambda: bool(self._pending), timeout=timedelta(seconds=timing.reread_seconds))
                except asyncio.TimeoutError:
                    pass
            await asyncio.sleep(timing.collect_seconds)  # let the others in the month ask too: one read for all
            batch = {**again, **self._pending}
            self._pending = {}
            outcome = await workflow.execute_activity(
                "hrsync.read", args=[company_code, list(batch.values())], task_queue=AGENT_TASK_QUEUE,
                start_to_close_timeout=timedelta(minutes=30), retry_policy=READ_RETRY)
            self._reads.append({"people": sorted(batch), "status": outcome["status"], "snapshot_id": outcome.get("snapshot_id")})
            again = {k: r for k, r in batch.items() if k in set(outcome["still_missing"])}
            if outcome["stop"]:  # nothing more the agent can do (no HR tool, or a person must act first)
                return outcome["status"]
