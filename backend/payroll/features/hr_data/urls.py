from django.urls import path

from . import views

urlpatterns = [
    path("catalogue", views.mapping_catalogue),
    path("hr-tool-profiles", views.hr_tool_profiles),
    path("companies/<int:company_id>/employees", views.employees),
    path("companies/<int:company_id>/connectors", views.connectors),
    path("companies/<int:company_id>/uploads", views.upload),
    path("companies/<int:company_id>/jobs", views.jobs),
    path("companies/<int:company_id>/snapshots", views.snapshots),
    path("connectors/<int:connector_id>", views.connector_detail),
    path("connectors/<int:connector_id>/discover", views.connector_discover),
    path("connectors/<int:connector_id>/confirm-screens", views.connector_confirm_recipe),
    path("connectors/<int:connector_id>/read", views.connector_read),
    path("jobs/<int:job_id>", views.job_detail),
    path("jobs/<int:job_id>/files", views.job_add_files),
    path("jobs/<int:job_id>/suggest-mapping", views.job_suggest),
    path("jobs/<int:job_id>/build-snapshot", views.job_build_snapshot),
    path("tables/<int:table_id>/confirm-mapping", views.table_confirm),
    path("tables/<int:table_id>/value-maps", views.table_value_maps),
    path("tables/<int:table_id>/month", views.table_month),
    path("snapshots/<int:snapshot_id>", views.snapshot_detail),
    path("snapshots/<int:snapshot_id>/approve", views.snapshot_approve),
    path("snapshots/<int:snapshot_id>/reject", views.snapshot_reject),
    path("media/<path:path>", views.agent_media),
]
