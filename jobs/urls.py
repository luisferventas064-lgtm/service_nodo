from django.urls import path
from . import views

urlpatterns = [
    path("match/<int:job_id>/", views.match_providers, name="match_providers"),
    path("assign/<int:job_id>/<int:provider_id>/", views.assign_provider, name="assign_provider"),
]
