"""Create the demo company, users, rules, settings and connectors. Safe to run again."""
import json

from django.conf import settings
from django.core.management.base import BaseCommand

from payroll.demo import setup


class Command(BaseCommand):
    help = "Seed Brickline Design Co., demo users (maker/checker), government rules and company settings (case A)."

    def add_arguments(self, parser):
        parser.add_argument("--case", default="A", help="Which answer-sheet case's company choices to start with (A or B)")
        parser.add_argument("--with-data", action="store_true", help="Also import the Brickline spreadsheet and approve it")
        parser.add_argument("--with-history", action="store_true", help="Also process April-June as already paid (implies --with-data)")

    def handle(self, *args, **opts):
        setup.ensure_users()
        n = setup.load_statutory_rules()
        seed = json.loads((setup.REPO / "data/brickline/seed.json").read_text())
        c = seed["company"]
        company = setup.ensure_company(c["code"], c["name"], c["state"], c["city"])
        choices = json.loads((setup.REPO / f"answer_sheets/case_{opts['case'].upper()}/choices.json").read_text())
        s = setup.set_company_settings(company, choices["settings"], choices["effective_from"],
                                       note=f"Initial company choices (answer-sheet case {opts['case'].upper()})")
        setup.ensure_demo_connectors(company)
        self.stdout.write(self.style.SUCCESS(f"Company '{company.name}' ready; {n} new rule versions, {s} new setting versions."))
        self.stdout.write("Users: maker/maker123 (prepares), checker/checker123 (approves), admin/admin123 (both, for setup only).")
        if opts["with_data"] or opts["with_history"]:
            if company.snapshots.filter(status="approved").exists():
                self.stdout.write("HR data already imported; skipping.")
            else:
                snap = setup.import_spreadsheet(company, setup.REPO / "data/brickline/brickline_hr_data.xlsx", log=self.stdout.write)
                self.stdout.write(self.style.SUCCESS(f"Imported and reviewed the Brickline spreadsheet (data #{snap.pk})."))
        if opts["with_history"]:
            from django.core.management import call_command

            call_command("run_history", company=company.code)
        from payroll.features.ai.client import is_model

        assistance = "model-assisted" if is_model() else "rule-based"
        self.stdout.write(f"Assistance: {assistance}. Workflow backend: {settings.WORKFLOW_BACKEND}.")
