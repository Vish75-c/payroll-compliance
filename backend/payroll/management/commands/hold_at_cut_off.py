"""Put people still waiting for HR data on hold once their month's cut-off day has come.

With Temporal, each month's workflow does this on the day. Without it (WORKFLOW_BACKEND=local), run this once a day,
e.g. from cron: ``manage.py hold_at_cut_off``.
"""
from django.core.management.base import BaseCommand

from payroll.features.payroll_runs import employees
from payroll.models import PayrollRun, RunEmployee


class Command(BaseCommand):
    help = "Hold everyone still waiting for HR data in open months whose cut-off day has come."

    def handle(self, *args, **options):
        open_runs = (PayrollRun.objects.filter(run_type=PayrollRun.TYPE_REGULAR, employee_states__status=RunEmployee.WAITING)
                     .exclude(status__in=[PayrollRun.AWAITING_APPROVAL, PayrollRun.APPROVED, PayrollRun.FINALIZED, PayrollRun.CANCELLED])
                     .distinct())
        for run in open_runs:
            held = employees.hold_waiting_at_cut_off(run.pk)
            if held:
                self.stdout.write(f"{run}: put on hold " + ", ".join(h.employee_code for h in held))
