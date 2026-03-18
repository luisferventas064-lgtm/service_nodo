from datetime import datetime, time

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.formats import date_format, time_format
from django.utils import timezone
from django.utils.translation import gettext as _

from assignments.models import JobAssignment
from jobs.events import create_job_event
from jobs.models import Job, JobEvent, JobProviderExclusion
from jobs import services as job_services
from jobs.services_state_transitions import (
    transition_assignment_status,
    transition_job_status,
)
from providers.models import Provider, ProviderService
from providers.availability import is_provider_effectively_available

from .views import (
    REQUEST_SERVICE_TIMING_LABELS,
    _billing_unit_display,
    _provider_services_request_area,
)


def _current_provider_from_session(request):
    provider_id = request.session.get("provider_id")
    if not provider_id:
        return None

    provider = Provider.objects.filter(pk=provider_id).first()
    if provider is None:
        request.session.pop("provider_id", None)
    return provider


def _postal_prefix(postal_code: str) -> str:
    normalized = str(postal_code or "").replace(" ", "").upper()
    return normalized[:3]


def _incoming_job_service_timing(job) -> str:
    for event in getattr(job, "incoming_events", []):
        payload = getattr(event, "payload_json", {}) or {}
        timing = str(payload.get("service_timing") or "").strip().lower()
        if timing:
            return timing

    if job.job_mode == Job.JobMode.SCHEDULED:
        return "scheduled"
    return "urgent"


def _incoming_job_price_value(job):
    for value in (
        job.requested_total_snapshot,
        job.requested_subtotal_snapshot,
        job.requested_base_line_total_snapshot,
    ):
        if value is not None:
            return value
    return None


def _incoming_job_schedule_display(job) -> str:
    if not job.scheduled_date:
        return _("ASAP")

    date_label = date_format(job.scheduled_date, "M j, Y")
    if job.scheduled_start_time:
        return f"{date_label} at {time_format(job.scheduled_start_time, 'g:i A')}"
    return date_label


def _incoming_job_billing_unit_display(job) -> str:
    raw_value = (
        (getattr(job, "requested_billing_unit_snapshot", "") or "").strip()
        or (getattr(job, "quoted_pricing_unit", "") or "").strip()
    )
    if not raw_value:
        return ""

    translated_units = {
        "hour": _("Per Hour"),
        "fixed": _("Fixed Price"),
        "sqm": _("Per Square Meter"),
        "km": _("Per Kilometer"),
        "day": _("Per Day"),
    }
    if raw_value in translated_units:
        return translated_units[raw_value]

    display_value = _billing_unit_display(raw_value)
    if display_value != raw_value:
        return _(display_value)
    return raw_value.replace("_", " ").title()


def _incoming_job_address_detail(job) -> str:
    line_bits = [
        (getattr(job, "address_line1", "") or "").strip(),
        (getattr(job, "address_line2", "") or "").strip(),
    ]
    location_bits = [
        (getattr(job, "city", "") or "").strip(),
        (getattr(job, "province", "") or "").strip(),
        (getattr(job, "postal_code", "") or "").strip(),
    ]
    detail_bits = [bit for bit in line_bits if bit]
    location_label = " ".join(bit for bit in location_bits if bit)
    if location_label:
        detail_bits.append(location_label)
    return ", ".join(detail_bits)


def _provider_has_service_capability(*, provider_id: int, job) -> bool:
    return ProviderService.objects.filter(
        provider_id=provider_id,
        service_type_id=job.service_type_id,
        is_active=True,
    ).exists()


def _job_is_ready_for_provider_incoming(job) -> bool:
    if job.job_mode != Job.JobMode.SCHEDULED or not job.scheduled_date:
        return True

    scheduled_time = job.scheduled_start_time or time.min
    scheduled_for = timezone.make_aware(
        datetime.combine(job.scheduled_date, scheduled_time),
        job.get_job_timezone(),
    )
    return scheduled_for <= timezone.now()


def _provider_is_eligible_for_incoming_job(*, provider, job) -> bool:
    if not is_provider_effectively_available(provider):
        return False
    if job.job_mode == Job.JobMode.ON_DEMAND and not getattr(provider, "accepts_urgent", True):
        return False
    if job.job_mode == Job.JobMode.SCHEDULED and not getattr(provider, "accepts_scheduled", True):
        return False
    if job.job_status != Job.JobStatus.WAITING_PROVIDER_RESPONSE:
        return False
    if job.selected_provider_id != provider.pk:
        return False
    if not job.has_pricing_snapshot():
        return False
    if not _job_is_ready_for_provider_incoming(job):
        return False
    if getattr(job, "_provider_is_excluded", False):
        return False
    if not _provider_has_service_capability(provider_id=provider.pk, job=job):
        return False
    return _provider_services_request_area(
        provider=provider,
        city=job.city,
        province=job.province,
        postal_code=job.postal_code,
    )


def _incoming_jobs_for_provider(provider):
    matching_service_exists = ProviderService.objects.filter(
        provider_id=provider.pk,
        service_type_id=OuterRef("service_type_id"),
        is_active=True,
    )
    exclusion_exists = JobProviderExclusion.objects.filter(
        job_id=OuterRef("pk"),
        provider_id=provider.pk,
    )
    incoming_event_queryset = JobEvent.objects.filter(
        event_type__in=[
            JobEvent.EventType.JOB_CREATED,
            JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
        ]
    ).order_by("-created_at", "-id")

    jobs = (
        Job.objects.filter(
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            selected_provider_id=provider.pk,
        )
        .annotate(_provider_has_service=Exists(matching_service_exists))
        .annotate(_provider_is_excluded=Exists(exclusion_exists))
        .filter(_provider_has_service=True, _provider_is_excluded=False)
        .select_related("client", "service_type", "provider_service")
        .prefetch_related(
            Prefetch("events", queryset=incoming_event_queryset, to_attr="incoming_events")
        )
        .order_by("created_at", "job_id")
    )

    eligible_jobs = []
    for job in jobs:
        if not _provider_is_eligible_for_incoming_job(provider=provider, job=job):
            continue

        timing = _incoming_job_service_timing(job)
        postal_prefix = _postal_prefix(job.postal_code)
        location_bits = [bit for bit in [job.city, postal_prefix] if bit]

        job.incoming_service_name = (
            (job.requested_subservice_name or "").strip()
            or (job.provider_service_name_snapshot or "").strip()
            or getattr(getattr(job, "service_type", None), "name", "")
            or _("Service request")
        )
        job.incoming_main_offer_name = (
            (job.provider_service_name_snapshot or "").strip()
            or getattr(getattr(job, "provider_service", None), "custom_name", "")
        )
        job.incoming_timing = timing
        job.incoming_timing_label = REQUEST_SERVICE_TIMING_LABELS.get(
            timing,
            timing.replace("_", " ").title(),
        )
        job.incoming_schedule_display = _incoming_job_schedule_display(job)
        job.incoming_location_label = " ".join(location_bits) if location_bits else "-"
        job.incoming_address_detail = _incoming_job_address_detail(job)
        job.incoming_unit_display = (job.address_line2 or "").strip()
        job.incoming_billing_unit_display = _incoming_job_billing_unit_display(job)
        job.incoming_access_notes = (job.access_notes or "").strip()
        job.incoming_price_value = _incoming_job_price_value(job)
        eligible_jobs.append(job)

    return eligible_jobs


def provider_incoming_jobs_view(request):
    provider = _current_provider_from_session(request)
    if provider is None:
        return redirect("provider_register")

    jobs = _incoming_jobs_for_provider(provider)
    return render(
        request,
        "provider/incoming_jobs.html",
        {
            "jobs": jobs,
            "incoming_jobs_count": len(jobs),
        },
    )


def handle_provider_accept_action(*, request, job, provider, redirect_name: str):
    if job.selected_provider_id != provider.pk:
        return HttpResponseForbidden(_("Not authorized."))
    if job.job_status != Job.JobStatus.WAITING_PROVIDER_RESPONSE:
        return HttpResponseBadRequest(_("Job not eligible for acceptance."))

    if not provider.is_operational:
        messages.warning(
            request,
            _("Complete your profile and add a service to accept jobs."),
        )
        request.session["provider_id"] = provider.pk
        return redirect("provider_dashboard")

    try:
        job_services.accept_provider_offer(
            job_id=job.job_id,
            provider_id=provider.provider_id,
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    except job_services.MarketplaceAcceptConflict as exc:
        return HttpResponseBadRequest(str(exc))
    except job_services.ProviderAcceptConflict as exc:
        return HttpResponseBadRequest(str(exc))
    except ValidationError as exc:
        return HttpResponseBadRequest("; ".join(exc.messages) or _("Invalid job state."))

    messages.success(request, _("Request accepted."))
    return redirect(redirect_name)



def handle_provider_decline_action(*, request, job, provider, redirect_name: str):
    if job.selected_provider_id != provider.pk:
        return HttpResponseForbidden(_("Not authorized."))
    if job.job_status != Job.JobStatus.WAITING_PROVIDER_RESPONSE:
        return HttpResponseForbidden(_("Invalid status."))

    with transaction.atomic():
        JobProviderExclusion.objects.get_or_create(
            job=job,
            provider=provider,
            defaults={"reason": JobProviderExclusion.Reason.DECLINED},
        )
        active_assignment = (
            job.assignments.filter(provider=provider, is_active=True)
            .order_by("-created_at")
            .first()
        )
        if active_assignment:
            transition_assignment_status(
                active_assignment,
                "cancelled",
                actor=JobEvent.ActorRole.PROVIDER,
                reason="provider_incoming_decline",
            )

        transition_job_status(
            job,
            Job.JobStatus.POSTED,
            actor=JobEvent.ActorRole.PROVIDER,
            reason="provider_incoming_decline",
        )
        job.selected_provider = None
        job.cancelled_by = Job.CancellationActor.PROVIDER
        job.cancel_reason = Job.CancelReason.PROVIDER_REJECTED
        job.save(
            update_fields=[
                "selected_provider",
                "cancelled_by",
                "cancel_reason",
                "updated_at",
            ]
        )
        create_job_event(
            job=job,
            event_type=JobEvent.EventType.PROVIDER_DECLINED,
            actor_role=JobEvent.ActorRole.PROVIDER,
            provider_id=provider.provider_id,
            payload={"source": "provider_incoming_decline"},
            job_status=Job.JobStatus.POSTED,
            note="provider declined incoming job",
        )

    messages.success(request, _("Request declined."))
    return redirect(redirect_name)


def handle_provider_decline_scheduled_action(*, request, job, provider, redirect_name: str):
    """
    Provider declines a scheduled job they were assigned to but have not started yet.
    The job returns to waiting_provider_response so the system can reassign it.
    The job is NOT cancelled - only the provider participation ends.
    """
    if job.selected_provider_id != provider.pk:
        return HttpResponseForbidden(_("Not authorized."))
    if job.job_status != Job.JobStatus.SCHEDULED_PENDING_ACTIVATION:
        return HttpResponseForbidden(_("Job is not in scheduled pending activation status."))

    with transaction.atomic():
        JobProviderExclusion.objects.get_or_create(
            job=job,
            provider=provider,
            defaults={"reason": JobProviderExclusion.Reason.DECLINED},
        )
        active_assignment = (
            job.assignments.filter(provider=provider, is_active=True)
            .order_by("-created_at")
            .first()
        )
        if active_assignment:
            transition_assignment_status(
                active_assignment,
                "cancelled",
                actor=JobEvent.ActorRole.PROVIDER,
                reason="provider_scheduled_decline",
            )

        transition_job_status(
            job,
            Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            actor=JobEvent.ActorRole.PROVIDER,
            reason="provider_scheduled_decline",
        )
        job.selected_provider = None
        job.save(update_fields=["selected_provider", "updated_at"])

        create_job_event(
            job=job,
            event_type=JobEvent.EventType.PROVIDER_DECLINED,
            actor_role=JobEvent.ActorRole.PROVIDER,
            provider_id=provider.provider_id,
            payload={"source": "provider_scheduled_decline"},
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            note="provider declined scheduled job before activation",
        )

    messages.success(request, _("You have been removed from this scheduled job."))
    return redirect(redirect_name)


def provider_accept_job_view(request, job_id):
    if request.method != "POST":
        return redirect("ui:provider_incoming_jobs")

    provider = _current_provider_from_session(request)
    if provider is None:
        return redirect("provider_register")

    job = get_object_or_404(Job, pk=job_id)
    return handle_provider_accept_action(
        request=request,
        job=job,
        provider=provider,
        redirect_name="ui:provider_incoming_jobs",
    )


def provider_decline_job_view(request, job_id):
    if request.method != "POST":
        return redirect("ui:provider_incoming_jobs")

    provider = _current_provider_from_session(request)
    if provider is None:
        return redirect("provider_register")

    job = get_object_or_404(Job, pk=job_id)
    return handle_provider_decline_action(
        request=request,
        job=job,
        provider=provider,
        redirect_name="ui:provider_incoming_jobs",
    )


def provider_decline_scheduled_job_view(request, job_id):
    if request.method != "POST":
        return redirect("ui:provider_jobs")

    provider = _current_provider_from_session(request)
    if provider is None:
        return redirect("provider_register")

    job = get_object_or_404(Job, pk=job_id)
    return handle_provider_decline_scheduled_action(
        request=request,
        job=job,
        provider=provider,
        redirect_name="ui:provider_jobs",
    )
