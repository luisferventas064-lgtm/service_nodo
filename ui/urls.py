from django.urls import path
from django.views.generic import RedirectView, TemplateView

from . import views

app_name = "ui"

urlpatterns = [
    path("", views.home, name="root_home"),
    path("home/", views.home, name="home"),
    path("terms/", views.terms_and_conditions, name="terms_and_conditions"),
    path("signup/", views.signup, name="signup"),
    path("login/", views.login_choice, name="login"),
    path("login/", views.login_choice, name="login_choice"),
    path("login/", views.login_choice, name="root_login"),
    path("login/client/", views.login_client, name="login_client"),
    path("login/provider/", views.login_provider, name="login_provider"),
    path("login/worker/", views.login_worker, name="login_worker"),
    path("resend-code/", views.resend_code, name="resend_code"),
    path("forgot-password/", views.forgot_password, name="forgot_password"),
    path("reset-password/", views.forgot_password, name="reset_password_request"),
    path("reset-password/verify/", views.reset_password_confirm, name="reset_password_verify"),
    path("reset-password/confirm/", views.reset_password_confirm, name="reset_password_confirm"),
    path("logout/", views.logout_view, name="logout"),
    path(
        "register/client/",
        RedirectView.as_view(pattern_name="client_register", permanent=False),
        name="client_register_alias",
    ),
    path(
        "register/provider/",
        RedirectView.as_view(pattern_name="provider_register", permanent=False),
        name="provider_register_alias",
    ),
    path(
        "client/profile/",
        RedirectView.as_view(pattern_name="client_profile", permanent=False),
        name="client_profile_alias",
    ),
    path(
        "provider/profile/",
        RedirectView.as_view(pattern_name="provider_profile", permanent=False),
        name="provider_profile_alias",
    ),
    path("portal/", views.portal_view, name="portal"),
    path("internal/analytics/marketplace/", views.marketplace_analytics_api_view, name="marketplace_analytics_api"),
    path("dashboard/marketplace/", views.marketplace_analytics_dashboard_view, name="marketplace_analytics_dashboard"),
    path("marketplace/", views.marketplace_search_view, name="marketplace_search"),
    path("marketplace/results/", views.marketplace_results_view, name="marketplace_results"),
    path("providers/nearby/<int:job_id>/", views.providers_nearby_view, name="providers_nearby_job"),
    path("providers/nearby/", views.providers_nearby_view, name="providers_nearby"),
    path("request/<int:provider_id>/", views.request_create_view, name="request_create"),
    path(
        "request/success/",
        TemplateView.as_view(template_name="request/success.html"),
        name="request_success",
    ),
    path("request/status/", views.request_status_lookup_view, name="request_status_lookup"),
    path("request/status/<int:job_id>/", views.request_status_view, name="request_status"),
    path("provider/jobs/", views.provider_jobs_view, name="provider_jobs"),
    path("provider/job/<int:job_id>/action/", views.provider_job_action_view, name="provider_job_action"),
    path("jobs/", views.jobs_list, name="jobs_list"),
    path("jobs/<int:job_id>/", views.job_detail, name="job_detail"),
    path("jobs/<int:job_id>/confirm-closed/", views.confirm_closed, name="confirm_closed"),
]
