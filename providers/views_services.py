from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import ProviderServiceForm
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
        return redirect("provider_complete_profile")

    return None


def provider_services_list(request):
    provider = _get_session_provider(request)
    if not provider:
        return redirect("provider_register")
    guard_response = _ensure_provider_can_manage_services(request, provider)
    if guard_response is not None:
        return guard_response

    services = list(
        ProviderService.objects.filter(provider=provider)
        .select_related("category")
        .order_by("category__name", "custom_name", "id")
    )
    for service in services:
        service.display_price = service.price_cents / 100

    return render(
        request,
        "providers/services_list.html",
        {
            "services": services,
            "provider": provider,
        },
    )


def provider_service_add(request):
    provider = _get_session_provider(request)
    if not provider:
        return redirect("provider_register")
    guard_response = _ensure_provider_can_manage_services(request, provider)
    if guard_response is not None:
        return guard_response

    if request.method == "POST":
        form = ProviderServiceForm(request.POST)
        if form.is_valid():
            service = form.save(commit=False)
            service.provider = provider
            service.save()
            messages.success(request, "Service added successfully.")
            return redirect("provider_services_list")
    else:
        form = ProviderServiceForm()

    return render(
        request,
        "providers/service_form.html",
        {
            "form": form,
            "mode": "add",
            "provider": provider,
        },
    )


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

    if request.method == "POST":
        form = ProviderServiceForm(request.POST, instance=service)
        if form.is_valid():
            form.save()
            messages.success(request, "Service updated.")
            return redirect("provider_services_list")
    else:
        form = ProviderServiceForm(instance=service)

    return render(
        request,
        "providers/service_form.html",
        {
            "form": form,
            "mode": "edit",
            "provider": provider,
            "service": service,
        },
    )


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

    return redirect("provider_services_list")
