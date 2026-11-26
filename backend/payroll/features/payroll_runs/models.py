from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from payroll_engine.periods import Period

from ..ai.models import AIInteraction
from ..companies.models import Company
from ..hr_data.models import DataSnapshot


class PayrollRun(models.Model):
    """One calculation of one month. A month can have one live regular run and any number of drafts."""

    TYPE_REGULAR = "regular"
    TYPE_DRAFT = "draft"

    DRAFT = "draft"
    CALCULATED = "calculated"
    AWAITING_APPROVAL = "awaiting_approval"
    REJECTED = "rejected"
    APPROVED = "approved"
    FINALIZED = "finalized"
    CANCELLED = "cancelled"
    STATUS = [
        (DRAFT, "Being prepared"),
        (CALCULATED, "Calculated"),
        (AWAITING_APPROVAL, "Waiting for approval"),
        (REJECTED, "Sent back"),
        (APPROVED, "Approved"),
        (FINALIZED, "Final"),
        (CANCELLED, "Cancelled"),
    ]
    LIVE_STATUSES = [DRAFT, CALCULATED, AWAITING_APPROVAL, REJECTED, APPROVED, FINALIZED]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="runs")
    period = models.CharField(max_length=7)
    run_type = models.CharField(max_length=10, default=TYPE_REGULAR)
    status = models.CharField(max_length=20, choices=STATUS, default=DRAFT)
    snapshot = models.ForeignKey(DataSnapshot, on_delete=models.PROTECT, related_name="runs")
    knowledge_date = models.DateField(help_text="Salary changes recorded after this date are not used (lets past months be re-created as they were known)")
    base_run = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="draft_reruns")
    policy_overrides = models.JSONField(default=dict, blank=True, help_text="What-if setting values, drafts only")
    day_count = models.PositiveSmallIntegerField(null=True, blank=True,
                                                 help_text="Days one month's salary is divided by for one day's pay, set for this month; empty: the company setting")
    note = models.TextField(blank=True)

    data_reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    data_reviewed_at = models.DateTimeField(null=True, blank=True)

    input = models.JSONField(null=True, blank=True)
    input_hash = models.CharField(max_length=64, blank=True)
    result = models.JSONField(null=True, blank=True)
    result_hash = models.CharField(max_length=64, blank=True)
    calculated_at = models.DateTimeField(null=True, blank=True)
    calculated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    calculation_count = models.IntegerField(default=0)

    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_result_hash = models.CharField(max_length=64, blank=True)
    submitted_result = models.JSONField(null=True, blank=True, help_text="Exactly what the finance head is asked to approve")

    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    files = models.JSONField(default=dict, blank=True)

    workflow_backend = models.CharField(max_length=20, blank=True)
    workflow_id = models.CharField(max_length=200, blank=True)
    last_error = models.TextField(blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period", "-id"]
        constraints = [
            # A month can be final only once.
            models.UniqueConstraint(
                fields=["company", "period"],
                condition=Q(run_type="regular", status="finalized"),
                name="one_final_run_per_month",
            ),
            # Only one regular run of a month is open at a time.
            models.UniqueConstraint(
                fields=["company", "period"],
                condition=Q(run_type="regular") & ~Q(status__in=["cancelled"]),
                name="one_live_regular_run_per_month",
            ),
        ]

    @property
    def period_obj(self) -> Period:
        return Period.parse(self.period)

    @property
    def is_locked(self) -> bool:
        return self.status in (self.AWAITING_APPROVAL, self.APPROVED, self.FINALIZED, self.CANCELLED)

    def __str__(self):
        return f"{self.company.code} {self.period} #{self.pk} ({self.status})"


class ChangeRequest(models.Model):
    """A change typed in plain English. The AI proposes; a person confirms; the engine calculates."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="change_requests")
    period = models.CharField(max_length=7)
    run = models.ForeignKey(PayrollRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="change_requests")
    text = models.TextField()
    ai = models.ForeignKey(AIInteraction, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    proposal = models.JSONField(default=dict, help_text="What the AI understood, after deterministic checks")
    status = models.CharField(max_length=20, default=PROPOSED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]


class RunEvent(models.Model):
    """Append-only audit trail of everything that happened to a month."""

    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="events")
    at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=150)
    action = models.CharField(max_length=50)
    message = models.TextField()
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["at", "id"]


class AbsenceDismissal(models.Model):
    """A day suggested as a possible unpaid absence (no check-in, no approved leave) that a maker said was not one,
    e.g. a forgotten punch or a day at a client's site. Kept so it is not suggested again, with who and why."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="absence_dismissals")
    period = models.CharField(max_length=7)
    employee_code = models.CharField(max_length=50)
    dates = models.JSONField(default=list)
    reason = models.TextField()
    dismissed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    dismissed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["period", "employee_code", "id"]
