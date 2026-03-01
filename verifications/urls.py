from django.urls import path

from . import views


urlpatterns = [
    path(
        "phone/request/",
        views.request_phone_verification,
        name="request_phone_verification",
    ),
    path(
        "phone/confirm/",
        views.confirm_phone_verification,
        name="confirm_phone_verification",
    ),
]
