from django.urls import path
from django.views.generic import TemplateView

from . import views

app_name = "ui"

urlpatterns = [
    path("", views.home, name="home"),
    path("portal/", views.portal_view, name="portal"),
    path("internal/analytics/marketplace/", views.marketplace_analytics_api_view, name="marketplace_analytics_api"),
    path("dashboard/marketplace/", views.marketplace_analytics_dashboard_view, name="marketplace_analytics_dashboard"),
    path("marketplace/", views.marketplace_search_view, name="marketplace_search"),
    path("marketplace/results/", views.marketplace_results_view, name="marketplace_results"),
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
