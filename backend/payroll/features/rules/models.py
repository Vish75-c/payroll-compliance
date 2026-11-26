from __future__ import annotations

from django.conf import settings
from django.db import models

from ..companies.models import Company


class PolicyVersion(models.Model):
    """One version of a government rule (company is null) or a company setting.

    Versions are append-only. To change a rule, add a version with a later start date.
    A version that a paid month has used can never be changed or deleted.
    """

    SCOPE_STATUTORY = "statutory"
    SCOPE_COMPANY = "company"

    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.CASCADE, related_name="policy_versions")
    scope = models.CharField(max_length=20, choices=[(SCOPE_STATUTORY, "Government rule"), (SCOPE_COMPANY, "Company setting")])
    key = models.CharField(max_length=50)
    jurisdiction = models.CharField(max_length=10, blank=True, help_text="IN for national rules, IN-KA for Karnataka, blank for company settings")
    effective_from = models.DateField()
    payload = models.JSONField()
    title = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["key", "jurisdiction", "effective_from", "id"]
        indexes = [models.Index(fields=["key", "jurisdiction", "effective_from"])]

    def save(self, *args, **kwargs):
        if self.pk and PolicyVersion.objects.filter(pk=self.pk).exists():
            raise ValueError("Rule and setting versions cannot be edited. Add a new version with a start date instead.")
        super().save(*args, **kwargs)

    def as_engine_dict(self) -> dict:
        return {
            "id": str(self.pk),
            "key": self.key,
            "scope": self.scope,
            "jurisdiction": self.jurisdiction,
            "effective_from": self.effective_from.isoformat(),
            "payload": self.payload,
            "title": self.title,
            "recorded_at": self.created_at.isoformat() if self.created_at else "",
        }

    def __str__(self):
        return f"{self.key} {self.jurisdiction} from {self.effective_from}"
