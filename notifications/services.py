from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from assignments.models import JobAssignment
from jobs.models import JobEvent


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
