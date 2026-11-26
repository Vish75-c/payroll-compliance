from __future__ import annotations

from django.conf import settings
from django.db import models

from ..companies.models import Company


class AIInteraction(models.Model):
    """Every AI answer is stored here before anything uses it, and a person reviews it."""

    PURPOSES = [
        ("field_mapping", "Suggest which HR field means what"),
        ("page_discovery", "Find the HR tool's screens"),
        ("screen_extraction", "Read records from an HR screen"),
        ("instruction", "Turn an instruction into a proposed change"),
        ("change_summary", "Explain what changed since last month"),
    ]
    STATUS = [
        ("pending_review", "Waiting for a person"),
        ("accepted", "Accepted by a person"),
        ("rejected", "Rejected by a person"),
        ("informational", "Shown to a person; affects no number"),
        ("error", "Failed"),
    ]
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.CASCADE, related_name="ai_interactions")
    purpose = models.CharField(max_length=30, choices=PURPOSES)
    provider = models.CharField(max_length=20, help_text="anthropic or rule-based")
    model = models.CharField(max_length=60, blank=True)
    input_hash = models.CharField(max_length=64, db_index=True)
    request = models.JSONField()
    response = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="pending_review")
    validation = models.JSONField(default=list, blank=True, help_text="Deterministic checks run on the AI answer")
    error = models.TextField(blank=True)
    latency_ms = models.IntegerField(default=0)
    usage = models.JSONField(default=dict, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reuse_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
