"""List the government dues of months that were final before dues were tracked. Running it again adds nothing."""
from django.core.management.base import BaseCommand

from payroll.models import PayrollRun
from payroll.features.dues import dues


class Command(BaseCommand):
    help = "Create the government dues (TDS, PF, ESI, PT) of final months that have none yet."

    def handle(self, *args, **opts):
        added = 0
        for run in PayrollRun.objects.filter(run_type=PayrollRun.TYPE_REGULAR, status=PayrollRun.FINALIZED, dues__isnull=True).distinct():
            added += len(dues.create_for_run(run))
            self.stdout.write(f"{run.company.code} {run.period}: dues listed.")
        if added:
            self.stdout.write(self.style.SUCCESS(f"{added} government dues listed for months that were already final."))
