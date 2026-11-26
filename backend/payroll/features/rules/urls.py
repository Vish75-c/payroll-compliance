from django.urls import path

from . import views

urlpatterns = [
    path("companies/<int:company_id>/policies", views.policies),
    path("companies/<int:company_id>/tools/wage-code", views.wage_code_simulator),
    path("policies/<int:version_id>", views.policy_delete),
]
