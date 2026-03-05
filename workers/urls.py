from django.urls import path

from . import views


urlpatterns = [
    path("register/worker/", views.worker_register, name="worker_register"),
    path("profile/", views.worker_profile, name="worker_profile"),
    path("jobs/", views.worker_jobs, name="worker_jobs"),
    path("activity/", views.worker_activity, name="worker_activity"),
    path("account/", views.worker_edit, name="worker_edit"),
    path("worker/profile/", views.worker_profile, name="worker_profile_legacy"),
    path("worker/jobs/", views.worker_jobs, name="worker_jobs_legacy"),
    path("worker/activity/", views.worker_activity, name="worker_activity_legacy"),
    path("worker/edit/", views.worker_edit, name="worker_edit_legacy"),
]
