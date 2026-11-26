from django.contrib import admin

from . import models

for model in (models.Company, models.Employee, models.Connector, models.IngestionJob, models.FieldMapping,
              models.DataSnapshot, models.PayrollRun, models.ChangeRequest, models.RunEvent, models.AIInteraction):
    admin.site.register(model)


@admin.register(models.PolicyVersion)
class PolicyVersionAdmin(admin.ModelAdmin):
    list_display = ("key", "jurisdiction", "company", "effective_from", "title")

    def has_change_permission(self, request, obj=None):
        return False  # versions are append-only
