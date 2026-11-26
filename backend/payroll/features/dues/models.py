from __future__ import annotations

from django.conf import settings
from django.db import models

from ..companies.models import Company
from ..payroll_runs.models import PayrollRun


class StatutoryDue(models.Model):
    """Money the company must deposit with the government for a final month: TDS, PF, ESI, professional tax.

    Created when the month is finalised, from its result; the amounts never change afterwards. Only the
    payment (date and reference) is recorded later. Whether it is due, overdue or paid is worked out from
    the dates, never stored."""

    TDS, PF, ESI, PT = "tds", "pf", "esi", "pt"
    KINDS = [(TDS, "TDS (income tax)"), (PF, "Provident Fund"), (ESI, "ESI"), (PT, "Professional tax")]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="dues")
    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name="dues")
    period = models.CharField(max_length=7)
    kind = models.CharField(max_length=5, choices=KINDS)
    jurisdiction = models.CharField(max_length=10, blank=True, help_text="State code for professional tax, e.g. KA")
    employee_amount = models.DecimalField(max_digits=14, decimal_places=2)
    employer_amount = models.DecimalField(max_digits=14, decimal_places=2)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    people = models.PositiveIntegerField(default=0)
    due_date = models.DateField()
    paid_on = models.DateField(null=True, blank=True)
    reference = models.CharField(max_length=60, blank=True, help_text="Challan, TRRN or CRN number of the payment")
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    paid_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date", "period", "kind", "jurisdiction"]
        constraints = [models.UniqueConstraint(fields=["run", "kind", "jurisdiction"], name="one_due_per_kind_per_month")]
