"""Import an HR spreadsheet from the command line (mapping: saved, given, or rule-based) and review it."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from payroll.models import Company
from payroll.demo import setup


class Command(BaseCommand):
    help = "Import an HR spreadsheet for a company and mark the data as reviewed."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--company", default="brickline")
        parser.add_argument("--mapping", help="JSON file {sheet name: {column: field}}")

    def handle(self, *args, **opts):
        company = Company.objects.filter(code=opts["company"]).first()
        if not company:
            raise CommandError("Unknown company; run seed_demo first.")
        try:
            snap = setup.import_spreadsheet(company, Path(opts["path"]), Path(opts["mapping"]) if opts.get("mapping") else None,
                                            log=self.stdout.write)
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Imported as data #{snap.pk}: {len(snap.data['employees'])} employees."))
