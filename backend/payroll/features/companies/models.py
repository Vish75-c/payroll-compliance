from __future__ import annotations

from django.conf import settings
from django.db import models


class Company(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    state = models.CharField(max_length=2, help_text="Two-letter state code of the main office, e.g. KA")
    city = models.CharField(max_length=100, blank=True)
    # Whether HR data of this company may be sent to an AI model. "off": only HR tool profiles, the
    # rule-based matcher and people map the data; nothing leaves the servers.
    AI_ALLOWED, AI_OFF = "allowed", "off"
    ai_mode = models.CharField(max_length=10, default=AI_ALLOWED,
                               choices=[(AI_ALLOWED, "AI may be used"), (AI_OFF, "No AI: profiles, rules and people only")])
    ai_mode_changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ai_mode_changed_at = models.DateTimeField(null=True, blank=True)
    # Registration numbers used when paying the government (shown with the dues and on the files).
    pan = models.CharField(max_length=10, blank=True)
    tan = models.CharField(max_length=10, blank=True, help_text="Tax deduction account number, needed to deposit TDS")
    pf_establishment_code = models.CharField(max_length=30, blank=True)
    esi_employer_code = models.CharField(max_length=30, blank=True)
    pay_day = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Day of the next month salaries are paid, e.g. 1")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name
