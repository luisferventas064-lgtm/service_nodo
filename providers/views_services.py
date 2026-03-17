from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .models import Provider, ProviderService


def _get_session_provider(request):
    provider_id = request.session.get("provider_id")
    if not provider_id:
        return None
    return Provider.objects.filter(pk=provider_id).first()


def _ensure_provider_can_manage_services(request, provider):
    if not provider.is_phone_verified:
        return redirect("provider_complete_profile")

    if not provider.profile_completed:
        messages.info(
            request,
            _("Please complete your profile to activate your provider account."),
        )
        return redirect("provider_complete_profile")

    return None


def provider_services_list(request):
    provider = _get_session_provider(request)
    if not provider:
        return redirect("provider_register")
    guard_response = _ensure_provider_can_manage_services(request, provider)
    if guard_response is not None:
        return guard_response

    messages.info(request, _("Services are now managed from the portal."))
    return redirect("portal:provider_services")


def provider_service_add(request):
    provider = _get_session_provider(request)
    if not provider:
        return redirect("provider_register")
    guard_response = _ensure_provider_can_manage_services(request, provider)
    if guard_response is not None:
        return guard_response

    messages.info(
        request,
        _("Use the portal service categories page to add a new service."),
    )
    return redirect("portal:provider_service_categories")


def provider_service_edit(request, service_id):
    provider = _get_session_provider(request)
    if not provider:
        return redirect("provider_register")
    guard_response = _ensure_provider_can_manage_services(request, provider)
    if guard_response is not None:
        return guard_response

    service = get_object_or_404(
        ProviderService,
        pk=service_id,
        provider=provider,
    )

    messages.info(request, _("This legacy edit page has moved to the portal."))
    return redirect("portal:provider_service_edit", service_id=service.id)


@require_POST
def provider_service_toggle(request, service_id):
    provider = _get_session_provider(request)
    if not provider:
        return redirect("provider_register")
    guard_response = _ensure_provider_can_manage_services(request, provider)
    if guard_response is not None:
        return guard_response

    service = get_object_or_404(
        ProviderService,
        pk=service_id,
        provider=provider,
    )

    service.is_active = not service.is_active
    service.save(update_fields=["is_active"])

    messages.info(
        request,
        _("Service status updated. Manage services in the portal."),
    )
    return redirect("portal:provider_services")
