from django.urls import path

from . import views


urlpatterns = [
    path("register/", views.client_register, name="client_register"),
    path("complete-profile/", views.client_complete_profile, name="client_complete_profile"),
    path("dashboard/", views.client_dashboard, name="client_dashboard"),
    path("profile/", views.client_profile, name="client_profile"),
]
