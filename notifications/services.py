from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from assignments.models import JobAssignment
from jobs.models import JobEvent
from providers.models import Provider, ProviderUser
from .models import PushDevice, PushDispatchAttempt
from .providers.fcm import send_fcm_push


def _get_provider_display_name(job):
    provider = job.selected_provider
    if provider is None:
        assignment = (
            JobAssignment.objects.select_related("provider")
            .filter(job_id=job.job_id, is_active=True)
            .order_by("-assignment_id")
            .first()
        )
        if assignment:
            provider = assignment.provider

    if provider is None:
        return "Unknown"

    return str(provider)


def send_auto_confirmation_email(job):
    confirmed_event = (
        job.events.filter(event_type=JobEvent.EventType.CLIENT_CONFIRMED)
        .order_by("-created_at")
        .first()
    )
    closure_type = "Closed by client"
    confirmed_at = "-"
    if confirmed_event:
        confirmed_at = timezone.localtime(confirmed_event.created_at).strftime(
            "%Y-%m-%d %H:%M"
        )
        if confirmed_event.note == "auto_timeout_72h":
            closure_type = "Closed automatically (72h timeout)"

    provider_name = _get_provider_display_name(job)
    service_type = str(job.service_type) if job.service_type_id else "-"
    subject = f"Service Closed - {job.public_reference}"

    message = f"""
Your service has been successfully closed.

Job Reference: {job.public_reference}
Provider: {provider_name}
Service Type: {service_type}
City: {job.city}
Mode: {job.job_mode}

Confirmed at: {confirmed_at}
{closure_type}

This service has been permanently closed.
No further actions are available.

Thank you,
NODO Team
"""

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [job.client.email],
        fail_silently=False,
    )


def send_dispute_resolution_email(job):
    provider_name = _get_provider_display_name(job)
    service_type = str(job.service_type) if job.service_type_id else "-"
    dispute = getattr(job, "dispute", None)
    public_resolution_note = (
        dispute.public_resolution_note if dispute and dispute.public_resolution_note else ""
    )
    resolved_at = "-"
    if dispute and dispute.resolved_at:
        resolved_at = timezone.localtime(dispute.resolved_at).strftime("%Y-%m-%d %H:%M")

    subject = f"Dispute Resolved - {job.public_reference}"
    message = f"""
Your dispute has been resolved.

Job Reference: {job.public_reference}
Provider: {provider_name}
Service Type: {service_type}
City: {job.city}
Mode: {job.job_mode}

Resolved at: {resolved_at}
{public_resolution_note}

Thank you,
NODO Team
"""

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [job.client.email],
        fail_silently=False,
    )


def send_quality_warning_email(provider):
    subject = "Quality Warning Notice"
    message = f"""
Hello {provider},

This is a formal quality warning notice.

Your account has reached the dispute threshold for the last 12 months.
You may continue receiving opportunities, but additional quality issues may
result in a temporary marketplace restriction.

Please review your service quality processes immediately.

Thank you,
NODO Team
"""

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [provider.email],
        fail_silently=False,
    )


def register_push_device_for_user(*, user, role: str, platform: str, token: str):
    return PushDevice.objects.update_or_create(
        token=token,
        defaults={
            "user": user,
            "role": role,
            "platform": platform,
            "is_active": True,
        },
    )


def _resolve_users_by_email(email: str):
    normalized_email = (email or "").strip()
    if not normalized_email:
        return []
    user_model = get_user_model()
    return list(
        user_model.objects.filter(email__iexact=normalized_email, is_active=True)
    )


def _resolve_provider_for_event(job_event):
    provider_id = getattr(job_event, "provider_id", None)
    if provider_id:
        provider = Provider.objects.filter(pk=provider_id).first()
        if provider is not None:
            return provider

    job = getattr(job_event, "job", None)
    if job is None:
        return None

    selected_provider = getattr(job, "selected_provider", None)
    if selected_provider is not None:
        return selected_provider

    active_assignment = (
        JobAssignment.objects.select_related("provider")
        .filter(job_id=job.job_id, is_active=True)
        .order_by("-assignment_id")
        .first()
    )
    return getattr(active_assignment, "provider", None)


def _resolve_provider_users(provider):
    if provider is None:
        return []

    provider_user_ids = list(
        ProviderUser.objects.filter(provider=provider, is_active=True).values_list(
            "user_id",
            flat=True,
        )
    )
    if provider_user_ids:
        user_model = get_user_model()
        users = list(user_model.objects.filter(pk__in=provider_user_ids, is_active=True))
        if users:
            return users

    return _resolve_users_by_email(getattr(provider, "email", ""))


def _resolve_client_users(client):
    if client is None:
        return []
    return _resolve_users_by_email(getattr(client, "email", ""))


def _resolve_job_event_recipients(job_event):
    job = getattr(job_event, "job", None)
    client = getattr(job, "client", None)
    provider = _resolve_provider_for_event(job_event)
    recipients = {}

    def add(users, role):
        for user in users:
            recipients[(user.pk, role)] = (user, role)

    if job_event.event_type == JobEvent.EventType.WAITING_PROVIDER_RESPONSE:
        add(_resolve_provider_users(provider), PushDevice.Role.PROVIDER)
    elif job_event.event_type == JobEvent.EventType.JOB_ACCEPTED:
        if job_event.actor_role == JobEvent.ActorRole.CLIENT:
            add(_resolve_provider_users(provider), PushDevice.Role.PROVIDER)
        else:
            add(_resolve_client_users(client), PushDevice.Role.CLIENT)
    elif job_event.event_type == JobEvent.EventType.JOB_COMPLETED:
        if job_event.actor_role in {
            JobEvent.ActorRole.PROVIDER,
            JobEvent.ActorRole.WORKER,
        }:
            add(_resolve_client_users(client), PushDevice.Role.CLIENT)
        else:
            add(_resolve_provider_users(provider), PushDevice.Role.PROVIDER)
    elif job_event.event_type == JobEvent.EventType.JOB_CANCELLED:
        if job_event.actor_role == JobEvent.ActorRole.CLIENT:
            add(_resolve_provider_users(provider), PushDevice.Role.PROVIDER)
        elif job_event.actor_role in {
            JobEvent.ActorRole.PROVIDER,
            JobEvent.ActorRole.WORKER,
        }:
            add(_resolve_client_users(client), PushDevice.Role.CLIENT)
        else:
            add(_resolve_client_users(client), PushDevice.Role.CLIENT)
            add(_resolve_provider_users(provider), PushDevice.Role.PROVIDER)

    return list(recipients.values())


def _build_job_event_push_payload(job_event):
    return {
        "event_type": job_event.event_type,
        "job_id": str(job_event.job_id),
        "visible_status": job_event.visible_status,
    }


def _current_push_provider() -> str:
    return (getattr(settings, "PUSH_PROVIDER", "stub") or "stub").strip().lower()


def _send_push_stub(*, device, payload):
    return {
        "ok": True,
        "provider": "stub",
        "device_id": device.id,
        "token": device.token,
        "payload": payload,
    }


def _send_push(*, device, payload):
    provider = _current_push_provider()
    if provider == "stub":
        return _send_push_stub(device=device, payload=payload)
    if provider == "fcm":
        return send_fcm_push(token=device.token, payload=payload)
    raise RuntimeError(f"Unsupported push provider '{provider}'")


def _push_dispatch_attempt_status(provider: str, response: dict) -> str:
    if not response.get("ok"):
        return PushDispatchAttempt.Status.FAILED
    if provider == "fcm":
        return PushDispatchAttempt.Status.SENT
    return PushDispatchAttempt.Status.STUB_SENT


@transaction.atomic
def dispatch_job_event_push(job_event):
    recipients = _resolve_job_event_recipients(job_event)
    if not recipients:
        return []

    payload = _build_job_event_push_payload(job_event)
    attempts = []
    for user, role in recipients:
        devices = PushDevice.objects.filter(
            user=user,
            role=role,
            is_active=True,
        ).order_by("pk")
        for device in devices:
            provider = _current_push_provider()
            try:
                response = _send_push(device=device, payload=payload)
            except Exception as exc:
                response = {
                    "ok": False,
                    "provider": provider,
                    "token": device.token,
                    "error": str(exc),
                }
            attempts.append(
                PushDispatchAttempt.objects.create(
                    job_event=job_event,
                    device=device,
                    status=_push_dispatch_attempt_status(provider, response),
                    payload_json=payload,
                    response_json=response,
                )
            )
    return attempts


def dispatch_job_event_push_by_id(job_event_id: int):
    job_event = (
        JobEvent.objects.select_related(
            "job",
            "job__client",
            "job__selected_provider",
        )
        .filter(pk=job_event_id)
        .first()
    )
    if job_event is None:
        return []
    return dispatch_job_event_push(job_event)
