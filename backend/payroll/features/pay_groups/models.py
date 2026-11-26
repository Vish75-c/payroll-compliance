from __future__ import annotations

from datetime import date

from django.conf import settings
from django.db import models

from ..companies.models import Company

# A cycle or an assignment dated this early counts from the company's first month.
FROM_THE_START = date(2000, 1, 1)


class PayGroup(models.Model):
    """People paid on the same cycle and the same day (e.g. office staff on the calendar month, paid on the 1st;
    factory staff on the 26th-to-25th cycle, paid on the 28th). Every company has one default group."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="pay_groups")
    code = models.SlugField(max_length=40)
    name = models.CharField(max_length=100)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_default", "id"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="one_pay_group_code_per_company")]

    def __str__(self):
        return f"{self.company.code}: {self.name}"


class PayGroupCycle(models.Model):
    """A group's cycle, pay day and who it covers, from a month on. A change is a new row, so the cycle a month was
    paid on is always known (and a change gets a transition period)."""

    MODES = [("calendar", "Calendar month"), ("attendance_cutoff", "Calendar month, unpaid days on a cycle"),
             ("shifted", "Shifted: salary and unpaid days on a cycle")]

    group = models.ForeignKey(PayGroup, on_delete=models.CASCADE, related_name="cycles")
    effective_from = models.DateField(help_text="The 1st of the first month it applies to")
    mode = models.CharField(max_length=20, choices=MODES, default="calendar")
    start_day = models.PositiveSmallIntegerField(default=1)
    pay_day = models.PositiveSmallIntegerField(null=True, blank=True)
    covers = models.JSONField(default=dict, blank=True, help_text='{"departments": [], "work_states": [], "designations": []}')
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["effective_from", "id"]


class EmployeePayGroup(models.Model):
    """Which group a person is in, from a month on. Rows from the HR data are added when reviewed data changes a
    person's group; a ``manual`` row is set by hand and the data never replaces it."""

    MANUAL, HR_DATA, RULE, DEFAULT = "manual", "hr_data", "rule", "default"
    SOURCES = [(MANUAL, "Set by hand"), (HR_DATA, "HR data"), (RULE, "Group rule"), (DEFAULT, "Default group")]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="pay_group_rows")
    employee_code = models.CharField(max_length=50)
    group = models.ForeignKey(PayGroup, on_delete=models.CASCADE, related_name="members")
    effective_from = models.DateField()
    source = models.CharField(max_length=10, choices=SOURCES)
    reason = models.CharField(max_length=200, blank=True, help_text="e.g. 'Department: Production'")
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["employee_code", "effective_from", "id"]
        indexes = [models.Index(fields=["company", "employee_code"])]
