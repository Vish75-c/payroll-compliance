"""Run the Temporal worker that executes payroll month workflows and their activities."""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand

log = logging.getLogger("payroll.workflow")


class Command(BaseCommand):
    help = "Run the Temporal worker for payroll months (keeps running; restart it any time)."

    def add_arguments(self, parser):
        parser.add_argument("--retry-seconds", type=int, default=5, help="Wait between connection attempts to Temporal")

    def handle(self, *args, **options):
        from temporalio.client import Client
        from temporalio.worker import Worker

        from payroll.features.payroll_runs.workflow.activities import AGENT, ALL
        from payroll.features.payroll_runs.workflow.definitions import (AGENT_TASK_QUEUE, EmployeeWorkflow, HRSyncWorkflow,
                                                                        PayrollMonthWorkflow)

        async def main():
            while True:
                try:
                    client = await Client.connect(settings.TEMPORAL_ADDRESS, namespace=settings.TEMPORAL_NAMESPACE)
                    break
                except Exception as exc:  # noqa: BLE001 - Temporal may still be starting
                    self.stderr.write(f"Temporal not reachable at {settings.TEMPORAL_ADDRESS} ({exc}); retrying...")
                    await asyncio.sleep(options["retry_seconds"])
            self.stdout.write(self.style.SUCCESS(
                f"Worker connected to {settings.TEMPORAL_ADDRESS}, task queue '{settings.TEMPORAL_TASK_QUEUE}'."))
            with ThreadPoolExecutor(max_workers=8) as pool, ThreadPoolExecutor(max_workers=1) as agent_pool:
                worker = Worker(client, task_queue=settings.TEMPORAL_TASK_QUEUE,
                                workflows=[PayrollMonthWorkflow, EmployeeWorkflow, HRSyncWorkflow], activities=ALL, activity_executor=pool)
                # The agent drives a browser: one read at a time, on its own queue, so a long read never holds up the months.
                agent = Worker(client, task_queue=AGENT_TASK_QUEUE, activities=AGENT, activity_executor=agent_pool,
                               max_concurrent_activities=1)
                self.stdout.write(f"Agent worker on task queue '{AGENT_TASK_QUEUE}' (one read at a time).")
                await asyncio.gather(worker.run(), agent.run())

        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            self.stdout.write("Worker stopped.")
