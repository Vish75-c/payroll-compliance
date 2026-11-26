from django.urls import path

from . import views

urlpatterns = [
    path("companies/<int:company_id>/dues", views.company_dues),
    path("dues/<int:due_id>/<str:action>", views.due_action),
]
