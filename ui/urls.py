from django.urls import path

from . import views

app_name = "ui"

urlpatterns = [
    path("", views.home, name="home"),
    path("jobs/", views.jobs_list, name="jobs_list"),
    path("jobs/<int:job_id>/", views.job_detail, name="job_detail"),
    path("jobs/<int:job_id>/confirm-closed/", views.confirm_closed, name="confirm_closed"),
]
