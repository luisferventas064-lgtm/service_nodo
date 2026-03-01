from django.urls import path

from . import views, views_services


urlpatterns = [
    path("register/", views.provider_register, name="provider_register"),
    path("complete-profile/", views.provider_complete_profile, name="provider_complete_profile"),
    path("complete-billing/", views.provider_complete_billing, name="provider_complete_billing"),
    path("dashboard/", views.provider_dashboard, name="provider_dashboard"),
    path("profile/", views.provider_profile, name="provider_profile"),
    path("services/", views_services.provider_services_list, name="provider_services_list"),
    path("services/add/", views_services.provider_service_add, name="provider_service_add"),
    path(
        "services/<int:service_id>/edit/",
        views_services.provider_service_edit,
        name="provider_service_edit",
    ),
    path(
        "services/<int:service_id>/toggle/",
        views_services.provider_service_toggle,
        name="provider_service_toggle",
    ),
]
