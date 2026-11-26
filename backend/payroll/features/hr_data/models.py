from __future__ import annotations

from django.conf import settings
from django.db import models

from ..ai.models import AIInteraction
from ..companies.models import Company


class Employee(models.Model):
    """Employee master, refreshed from each approved snapshot. Used to look up names."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="employees")
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=200)
    work_state = models.CharField(max_length=2, blank=True)
    gender = models.CharField(max_length=1, blank=True)
    date_of_joining = models.DateField(null=True, blank=True)
    date_of_exit = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("company", "code")]
        ordering = ["code"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class Connector(models.Model):
    """A source of HR data: an HR tool read through its screens, or spreadsheet uploads."""

    KIND_BROWSER = "browser"
    KIND_SPREADSHEET = "spreadsheet"
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="connectors")
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=[(KIND_BROWSER, "HR tool screens"), (KIND_SPREADSHEET, "Spreadsheet upload")])
    system_name = models.CharField(max_length=100, help_text="e.g. Horilla, Demo HRMS, Spreadsheet")
    profile = models.CharField(max_length=50, blank=True, help_text="Known HR tool (integrations/<profile>/profile.json), if any")
    base_url = models.URLField(blank=True)
    username = models.CharField(max_length=200, blank=True)
    password = models.CharField(max_length=200, blank=True, help_text="Write-only through the API")
    # Pages to read, learned once (by the AI or a person) and confirmed by a person:
    # {"login_url": "...", "datasets": {"employees": {"url": "...", "follow_detail_links": false}, ...}}
    recipe = models.JSONField(default=dict, blank=True)
    recipe_confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    recipe_confirmed_at = models.DateTimeField(null=True, blank=True)
    recipe_ai = models.ForeignKey(AIInteraction, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.system_name})"


class IngestionJob(models.Model):
    STATUS = [
        ("queued", "Queued"),
        ("running", "Reading"),
        ("needs_mapping", "Waiting for field mapping"),
        ("ready", "Ready to build a snapshot"),
        ("done", "Snapshot created"),
        ("failed", "Failed"),
    ]
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="ingestion_jobs")
    connector = models.ForeignKey(Connector, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs")
    kind = models.CharField(max_length=20)
    source_system = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS, default="queued")
    file = models.FileField(upload_to="uploads/", blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    log = models.JSONField(default=list, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class RawTable(models.Model):
    """One table exactly as read: a spreadsheet sheet or an HR screen (all its pages)."""

    job = models.ForeignKey(IngestionJob, on_delete=models.CASCADE, related_name="tables")
    name = models.CharField(max_length=200)
    dataset_hint = models.CharField(max_length=50, blank=True)
    source_url = models.CharField(max_length=500, blank=True)
    headers = models.JSONField(default=list)
    rows = models.JSONField(default=list)
    fingerprint = models.CharField(max_length=64)
    read_info = models.JSONField(default=dict, blank=True, help_text="pages read, counts shown on screen, method used")
    mapping = models.ForeignKey("FieldMapping", null=True, blank=True, on_delete=models.SET_NULL, related_name="tables")

    class Meta:
        ordering = ["id"]


class FieldMapping(models.Model):
    """Which source column means which payroll field. Confirmed once by a person, then reused."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="field_mappings")
    source_system = models.CharField(max_length=100)
    table_name = models.CharField(max_length=200)
    fingerprint = models.CharField(max_length=64, db_index=True)
    headers = models.JSONField(default=list)
    mapping = models.JSONField(default=dict, help_text="{source column: target field}")
    value_maps = models.JSONField(default=dict, blank=True, help_text="e.g. which leave types are unpaid")
    options = models.JSONField(default=dict, blank=True, help_text="e.g. date order DMY")
    suggested_by = models.ForeignKey(AIInteraction, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-confirmed_at", "-id"]


class DataSnapshot(models.Model):
    """HR data in one standard shape, whatever it came from. Immutable once created."""

    STATUS = [
        ("pending_review", "Waiting for the finance team employee's review"),
        ("approved", "Reviewed by the finance team employee"),
        ("rejected", "Rejected"),
    ]
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="snapshots")
    job = models.ForeignKey(IngestionJob, null=True, blank=True, on_delete=models.SET_NULL, related_name="snapshots")
    source_kind = models.CharField(max_length=20)
    source_system = models.CharField(max_length=100)
    data = models.JSONField()
    content_hash = models.CharField(max_length=64)
    checks = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUS, default="pending_review")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def has_blocking_issues(self) -> bool:
        return any(c.get("level") == "error" for c in self.checks)
