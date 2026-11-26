from django.urls import path

from . import views

urlpatterns = [
    path("companies/<int:company_id>/pay-groups", views.pay_groups),
    path("companies/<int:company_id>/pay-group-employees", views.employees),
    path("companies/<int:company_id>/pay-group-assignments", views.assignments),
    path("pay-groups/<int:group_id>/cycles", views.pay_group_cycles),
]
