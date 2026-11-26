from django.urls import include, path

urlpatterns = [
    path("", include("payroll.features.accounts.urls")),
    path("", include("payroll.features.companies.urls")),
    path("", include("payroll.features.onboarding.urls")),
    path("", include("payroll.features.dues.urls")),
    path("", include("payroll.features.hr_data.urls")),
    path("", include("payroll.features.payroll_runs.urls")),
    path("", include("payroll.features.rules.urls")),
    path("", include("payroll.features.ai.urls")),
]
