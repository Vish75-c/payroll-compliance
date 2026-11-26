from django.urls import path

from . import views

urlpatterns = [
    path("states", views.states),
    path("companies", views.companies),
    path("companies/<int:company_id>", views.company_detail),
    path("companies/<int:company_id>/overview", views.company_overview),
]
