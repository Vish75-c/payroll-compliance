"""Process months that were already paid (e.g. April-June) so that July has a history to compare with."""
from django.core.management.base import BaseCommand, CommandError

from payroll_engine.money import d, inr

from payroll.models import Company, PayrollRun
from payroll.demo import setup
from payroll.shared.permissions import RuleViolation


class Command(BaseCommand):
    help = "Calculate and approve already-paid months (default 2025-04..2025-06) with two service accounts."

    def add_arguments(self, parser):
        parser.add_argument("--company", default="brickline")
        parser.add_argument("--months", nargs="+", default=["2025-04", "2025-05", "2025-06"])

    def handle(self, *args, **opts):
        company = Company.objects.filter(code=opts["company"]).first()
        if not company:
            raise CommandError(f"No company '{opts['company']}'. Run seed_demo first.")
        for month in opts["months"]:
            if PayrollRun.objects.filter(company=company, period=month, run_type="regular", status="finalized").exists():
                self.stdout.write(f"{month}: already final, skipped.")
                continue
            try:
                run = setup.run_month(company, month, log=self.stdout.write)
            except RuleViolation as exc:
                raise CommandError(f"{month}: {exc.message}") from exc
            paid = setup.record_past_payments(run)
            self.stdout.write(self.style.SUCCESS(
                f"{month}: final. Net pay {inr(d(run.result['totals']['net_pay']))} for {run.result['employee_count']} employees "
                f"(salary known as of {run.knowledge_date}). {paid} government payments recorded as made on time."))
