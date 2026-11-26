from django.urls import path

from . import views

urlpatterns = [
    path("companies/<int:company_id>/ai-log", views.ai_log),
]
