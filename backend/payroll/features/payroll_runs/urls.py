from django.urls import path

from . import views

urlpatterns = [
    path("companies/<int:company_id>/runs", views.runs),
    path("companies/<int:company_id>/day-count", views.day_count),
    path("runs/<int:run_id>", views.run_detail),
    path("runs/<int:run_id>/verify", views.run_verify),
    path("runs/<int:run_id>/draft-rerun", views.run_draft),
    path("runs/<int:run_id>/compare/<int:other_id>", views.run_compare),
    path("runs/<int:run_id>/explain", views.run_explain),
    path("runs/<int:run_id>/instructions", views.run_instruction),
    path("runs/<int:run_id>/add-by-hand", views.run_add_by_hand),
    path("runs/<int:run_id>/possible-absences", views.run_possible_absences),
    path("runs/<int:run_id>/possible-absences/dismiss", views.run_dismiss_absence),
    path("runs/<int:run_id>/files/<str:name>", views.run_file),
    path("runs/<int:run_id>/export", views.run_register_preview),
    path("runs/<int:run_id>/<str:action>", views.run_action),
    path("changes/<int:change_id>/<str:decision>", views.change_decide),
]
