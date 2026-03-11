from collections import OrderedDict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from compliance.services import evaluate_provider_compliance
from core.auth_session import LEGACY_ROLE_SESSION_KEYS, SESSION_KEY_ROLE, require_role, set_session
from jobs.models import Job
from providers.models import Provider, ProviderService
from service_type.models import ServiceType

from .forms import ProviderServiceCreateForm, _normalize_name


def _get_session_role(request):
    role = request.session.get(SESSION_KEY_ROLE)
    if role:
        return role

    for legacy_role, legacy_key in LEGACY_ROLE_SESSION_KEYS.items():
        legacy_id = request.session.get(legacy_key)
        if legacy_id:
            set_session(request, role=legacy_role, profile_id=legacy_id)
            return legacy_role

    return None


def home(request):
    role = _get_session_role(request)
    if role is None:
        messages.error(request, "Please log in to access your portal.")
        return redirect("ui:root_login")

    if role == "client":
        return redirect("portal:client_dashboard")

    if role == "provider":
        return redirect("portal:provider_dashboard")

    if role == "worker":
        return redirect("portal:worker_dashboard")

    return redirect("portal:internal")


def internal(request):
    return render(request, "portal/index.html")


def _get_provider_from_session(request):
    provider_id = request.session.get("nodo_profile_id") or request.session.get("provider_id")
    if not provider_id:
        return None
    return Provider.objects.filter(pk=provider_id).first()


def _get_provider_compliance_result(provider, service_type):
    return evaluate_provider_compliance(
        provider=provider,
        province_code=provider.province,
        service_type=service_type,
    )


@require_role("provider")
def provider_dashboard_view(request):
    provider = _get_provider_from_session(request)
    if provider is None:
        messages.error(request, "Provider profile not found. Please complete your profile.")
        return redirect("ui:root_login")

    qs = Job.objects.filter(selected_provider=provider)

    open_statuses = [
        Job.JobStatus.POSTED,
        Job.JobStatus.WAITING_PROVIDER_RESPONSE,
    ]
    pending_statuses = [
        Job.JobStatus.PENDING_CLIENT_DECISION,
        Job.JobStatus.PENDING_PROVIDER_CONFIRMATION,
        Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
        Job.JobStatus.ASSIGNED,
        Job.JobStatus.IN_PROGRESS,
        Job.JobStatus.HOLD,
    ]
    done_statuses = [
        Job.JobStatus.COMPLETED,
        Job.JobStatus.CONFIRMED,
    ]
    cancelled_statuses = [
        Job.JobStatus.CANCELLED,
        Job.JobStatus.EXPIRED,
    ]

    counts = qs.aggregate(
        total=Count("job_id"),
        open=Count("job_id", filter=Q(job_status__in=open_statuses)),
        pending=Count("job_id", filter=Q(job_status__in=pending_statuses)),
        completed=Count("job_id", filter=Q(job_status__in=done_statuses)),
        cancelled=Count("job_id", filter=Q(job_status__in=cancelled_statuses)),
    )

    revenue_cents = (
        qs.filter(job_status__in=done_statuses)
        .aggregate(total=Sum("quoted_total_price_cents"))
        .get("total")
        or 0
    )
    revenue_amount = revenue_cents / 100

    recent_jobs = list(qs.order_by("-job_id")[:10])
    for job in recent_jobs:
        job.quoted_total_amount = (job.quoted_total_price_cents or 0) / 100

    context = {
        "provider": provider,
        "counts": counts,
        "revenue_cents": revenue_cents,
        "revenue_amount": revenue_amount,
        "recent_jobs": recent_jobs,
    }
    return render(request, "portal/provider_dashboard.html", context)


@require_role("provider")
def provider_services_view(request):
    provider = _get_provider_from_session(request)
    if provider is None:
        messages.error(request, "Provider profile not found. Please complete your profile.")
        return redirect("ui:root_login")

    provider.evaluate_profile_completion()
    provider.refresh_from_db(fields=["profile_completed"])

    if not provider.profile_completed:
        messages.info(request, "Please complete your profile to activate your provider account.")
        return redirect("provider_complete_profile")

    service_type_id = request.GET.get("service_type_id")

    services_qs = (
        ProviderService.objects
        .filter(provider=provider)
        .select_related("service_type")
    )

    selected_service_type = None
    if service_type_id:
        selected_service_type = ServiceType.objects.filter(
            service_type_id=service_type_id,
            is_active=True,
        ).first()
        if selected_service_type:
            services_qs = services_qs.filter(service_type=selected_service_type)

    services = services_qs.order_by("service_type__name", "custom_name")

    grouped_services = OrderedDict()

    for service in services:
        service.display_price = service.price_cents / 100
        service.compliance_result = _get_provider_compliance_result(
            provider,
            service.service_type,
        )
        service_type_name = service.service_type.name.strip()
        is_addon = (service.custom_name or "").startswith("ADDON: ")

        if service_type_name not in grouped_services:
            grouped_services[service_type_name] = {
                "service_type": service.service_type,
                "main_offers": [],
                "addons": [],
            }

        if is_addon:
            grouped_services[service_type_name]["addons"].append(service)
        else:
            grouped_services[service_type_name]["main_offers"].append(service)

    available_service_types = ServiceType.objects.filter(is_active=True).order_by("name")

    context = {
        "provider": provider,
        "grouped_services": grouped_services,
        "available_service_types": available_service_types,
        "selected_service_type": selected_service_type,
        "is_filtered": selected_service_type is not None,
    }
    return render(request, "portal/provider_services.html", context)


@require_role("provider")
def provider_service_categories_view(request):
    provider = _get_provider_from_session(request)
    if provider is None:
        messages.error(request, "Provider profile not found. Please complete your profile.")
        return redirect("ui:root_login")

    provider.evaluate_profile_completion()
    provider.refresh_from_db(fields=["profile_completed"])

    if not provider.profile_completed:
        messages.info(request, "Please complete your profile to activate your provider account.")
        return redirect("provider_complete_profile")

    service_types = ServiceType.objects.filter(is_active=True).order_by("name")

    used_service_type_ids = set(
        ProviderService.objects.filter(provider=provider)
        .values_list("service_type_id", flat=True)
        .distinct()
    )

    your_categories = []
    other_categories = []

    for st in service_types:
        if st.service_type_id in used_service_type_ids:
            your_categories.append(st)
        else:
            other_categories.append(st)

    context = {
        "provider": provider,
        "your_categories": your_categories,
        "other_categories": other_categories,
    }
    return render(request, "portal/provider_service_categories.html", context)


@require_role("provider")
def provider_service_add_view(request, service_type_id):
    provider = _get_provider_from_session(request)
    if not provider:
        messages.error(request, "Please sign in again.")
        return redirect("ui:root_login")

    provider.evaluate_profile_completion()
    provider.refresh_from_db(fields=["profile_completed"])

    if not provider.profile_completed:
        messages.info(request, "Complete your provider profile before managing services.")
        return redirect("provider_complete_profile")

    service_type = get_object_or_404(ServiceType, pk=service_type_id, is_active=True)

    if request.method == "POST":
        form = ProviderServiceCreateForm(
            request.POST,
            service_type_name=service_type.name,
        )
        if form.is_valid():
            custom_name = form.cleaned_data["custom_name"]
            normalized_name = _normalize_name(custom_name)

            duplicate_exists = ProviderService.objects.filter(
                provider=provider,
                service_type=service_type,
            ).exclude(
                custom_name__isnull=True,
            ).exists()

            if duplicate_exists:
                existing_services = ProviderService.objects.filter(
                    provider=provider,
                    service_type=service_type,
                )
                for existing in existing_services:
                    if _normalize_name(existing.custom_name or "") == normalized_name:
                        form.add_error(
                            "custom_name",
                            "You already have a service with this name in this category.",
                        )
                        break

            if not form.errors:
                service = form.save(commit=False)
                service.provider = provider
                service.service_type = service_type
                service.save()
                messages.success(request, "Service added successfully.")
                return redirect("portal:provider_services")
    else:
        form = ProviderServiceCreateForm(service_type_name=service_type.name)

    return render(
        request,
        "portal/provider_service_add.html",
        {
            "provider": provider,
            "service_type": service_type,
            "form": form,
            "compliance_result": _get_provider_compliance_result(provider, service_type),
            "compliance_province_code": provider.province,
        },
    )


@require_role("provider")
def provider_service_edit_view(request, service_id):
    provider = _get_provider_from_session(request)
    if not provider:
        messages.error(request, "Please sign in again.")
        return redirect("ui:root_login")

    provider.evaluate_profile_completion()
    provider.refresh_from_db(fields=["profile_completed"])

    if not provider.profile_completed:
        messages.info(request, "Complete your provider profile before managing services.")
        return redirect("provider_complete_profile")

    service = get_object_or_404(
        ProviderService.objects.select_related("service_type"),
        pk=service_id,
        provider=provider,
    )

    if request.method == "POST":
        form = ProviderServiceCreateForm(
            request.POST,
            instance=service,
            service_type_name=service.service_type.name,
        )
        if form.is_valid():
            custom_name = form.cleaned_data["custom_name"]
            normalized_name = _normalize_name(custom_name)

            sibling_services = ProviderService.objects.filter(
                provider=provider,
                service_type=service.service_type,
            ).exclude(pk=service.pk)

            for existing in sibling_services:
                if _normalize_name(existing.custom_name or "") == normalized_name:
                    form.add_error(
                        "custom_name",
                        "You already have a service with this name in this category.",
                    )
                    break

            if not form.errors:
                form.save()
                messages.success(request, "Service updated successfully.")
                return redirect("portal:provider_services")
    else:
        form = ProviderServiceCreateForm(
            instance=service,
            service_type_name=service.service_type.name,
        )

    return render(
        request,
        "portal/provider_service_edit.html",
        {
            "provider": provider,
            "service": service,
            "service_type": service.service_type,
            "form": form,
            "compliance_result": _get_provider_compliance_result(provider, service.service_type),
            "compliance_province_code": provider.province,
        },
    )


@require_role("provider")
@require_POST
def provider_service_toggle_view(request, service_id: int):
    provider = _get_provider_from_session(request)
    if not provider:
        messages.error(request, "Please sign in again.")
        return redirect("ui:root_login")

    provider.evaluate_profile_completion()
    provider.refresh_from_db(fields=["profile_completed"])

    if not provider.profile_completed:
        messages.info(request, "Complete your provider profile before managing services.")
        return redirect("provider_complete_profile")

    service = get_object_or_404(
        ProviderService,
        pk=service_id,
        provider=provider,
    )

    if request.method == "POST":
        service.is_active = not service.is_active
        service.save(update_fields=["is_active"])
        messages.success(
            request,
            "Service activated successfully." if service.is_active else "Service deactivated successfully.",
        )

    return redirect("portal:provider_services")


def client_dashboard_alias(request):
    role = _get_session_role(request)
    if role != "client":
        messages.error(request, "Please log in as client.")
        return redirect("ui:root_login")
    return redirect("client_dashboard")


def provider_dashboard_alias(request):
    role = _get_session_role(request)
    if role != "provider":
        messages.error(request, "Please log in as provider.")
        return redirect("ui:root_login")
    return redirect("provider_dashboard")


def worker_dashboard_alias(request):
    role = _get_session_role(request)
    if role != "worker":
        messages.error(request, "Please log in as worker.")
        return redirect("ui:root_login")
    return redirect("worker_jobs")
