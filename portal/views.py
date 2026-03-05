from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render

from core.auth_session import LEGACY_ROLE_SESSION_KEYS, SESSION_KEY_ROLE, require_role, set_session
from jobs.models import Job
from providers.models import Provider, ProviderService
from service_type.models import ServiceType

from .forms import ProviderServiceCreateForm


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
    if request.session.get("nodo_role") and request.session.get("nodo_role") != "provider":
        return redirect("ui:portal")

    provider = _get_provider_from_session(request)
    if not provider:
        messages.error(request, "Provider profile not found. Please sign up again.")
        return redirect("provider_register")

    provider.evaluate_profile_completion()
    provider.refresh_from_db(fields=["profile_completed"])

    if not provider.profile_completed:
        messages.info(request, "Please complete your profile to activate your provider account.")
        return redirect("provider_complete_profile")

    my_services = list(
        ProviderService.objects.filter(provider=provider)
        .select_related("service_type")
        .order_by("-is_active", "service_type__name", "custom_name")
    )
    for service in my_services:
        service.display_price = service.price_cents / 100

    used_type_ids = list(
        ProviderService.objects.filter(provider=provider).values_list("service_type_id", flat=True)
    )
    available_types = ServiceType.objects.filter(is_active=True).exclude(
        service_type_id__in=used_type_ids
    ).order_by("name")

    return render(
        request,
        "portal/provider_services.html",
        {
            "provider": provider,
            "my_services": my_services,
            "available_types": available_types,
        },
    )


@require_role("provider")
def provider_service_add_view(request, service_type_id: int):
    if request.session.get("nodo_role") and request.session.get("nodo_role") != "provider":
        return redirect("ui:portal")

    provider = _get_provider_from_session(request)
    if not provider:
        messages.error(request, "Provider profile not found. Please sign up again.")
        return redirect("provider_register")

    provider.evaluate_profile_completion()
    provider.refresh_from_db(fields=["profile_completed"])

    if not provider.profile_completed:
        messages.info(request, "Please complete your profile to activate your provider account.")
        return redirect("provider_complete_profile")

    service_type = get_object_or_404(ServiceType, service_type_id=service_type_id, is_active=True)

    if ProviderService.objects.filter(provider=provider, service_type=service_type).exists():
        messages.info(request, "You already added this service type.")
        return redirect("portal:provider_services")

    if request.method == "POST":
        form = ProviderServiceCreateForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.provider = provider
            obj.service_type = service_type
            obj.is_active = True
            obj.save()
            messages.success(request, "Service added successfully.")
            return redirect("portal:provider_services")
    else:
        form = ProviderServiceCreateForm()

    return render(
        request,
        "portal/provider_service_add.html",
        {
            "provider": provider,
            "service_type": service_type,
            "form": form,
        },
    )


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
