from django.urls import path
from . import views

urlpatterns = [
    path("jobs/match/<int:job_id>/", views.match_providers, name="match_providers"),
    path("jobs/assign/<int:job_id>/<int:provider_id>/", views.assign_provider, name="assign_provider"),
    path("api/jobs/<int:job_id>/start", views.api_job_start, name="api_job_start"),
    path("api/jobs/<int:job_id>/close", views.api_job_close, name="api_job_close"),
    path("api/jobs/<int:job_id>/extras", views.api_job_add_extra, name="api_job_add_extra"),
]
