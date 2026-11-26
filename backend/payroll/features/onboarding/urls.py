from django.urls import path

from . import views

urlpatterns = [
    path("companies/<int:company_id>/onboarding", views.company_onboarding),
    path("companies/<int:company_id>/opening-balances", views.opening_balances),
]
