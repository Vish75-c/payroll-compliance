from __future__ import annotations

from django.conf import settings
from django.db import models

from ..companies.models import Company


class OpeningBalance(models.Model):
    """Pay and TDS of an employee earlier in the financial year, before the company used this system.
    TDS for the rest of the year is spread from these, as if those months had been paid here."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="opening_balances")
    fy_start = models.DateField(help_text="1 April of the financial year")
    employee_code = models.CharField(max_length=50)
    gross = models.DecimalField(max_digits=14, decimal_places=2)
    tds = models.DecimalField(max_digits=14, decimal_places=2)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    entered_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["employee_code"]
        constraints = [models.UniqueConstraint(fields=["company", "fy_start", "employee_code"], name="one_opening_balance_per_person")]
