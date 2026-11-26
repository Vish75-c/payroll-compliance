"""Temporal workflow for one payroll month. Orchestration only: no Django, no I/O, deterministic.

The month waits for commands (signals) from people: calculate, submit, approve, reject,
cancel. Waiting costs nothing: no process is busy, and the waiting state lives in
Temporal's database, so it survives restarts of the API, the worker and Temporal itself.
Each step runs as an activity that re-checks the rules in the payroll database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

TASK_QUEUE_DEFAULT = "payroll-months"

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

    @workflow.signal(name="command")
    def command(self, cmd: Command) -> None:
        self._queue.append(cmd)

    @workflow.query(name="state")
    def state(self) -> dict:
        s = self._state
        return {"status": s.status if s else "starting", "pending_commands": len(self._queue),
                "last_error": s.last_error if s else "", "handled": len(s.handled) if s else 0}

    @workflow.run
    async def run(self, run_id: int) -> str:
        self._state = MonthState(run_id=run_id)
        while True:
            await workflow.wait_condition(lambda: len(self._queue) > 0)
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
            if cmd.name == "approve":
                # Approved: finalise and write the files. Finalising twice is a no-op. If it still fails after
                # its retries, the month stays approved and the workflow keeps waiting for a "finalize" command.
                if await self._finalize(run_id, cmd.actor_id):
                    return "finalized"
            if cmd.name == "finalize":  # a retry after a failed finalise, and it succeeded
                return "finalized"
            if cmd.name == "cancel":
                return "cancelled"
            if workflow.info().get_current_history_length() > 5000:
                workflow.continue_as_new(run_id)

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
