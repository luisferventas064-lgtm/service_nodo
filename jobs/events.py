from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Job, JobEvent

VISIBLE_JOB_STATUS_LABELS = {
    Job.JobStatus.DRAFT: _("Waiting for provider response"),
    Job.JobStatus.POSTED: _("Waiting for provider response"),
    Job.JobStatus.SCHEDULED_PENDING_ACTIVATION: _("Request submitted"),
    Job.JobStatus.WAITING_PROVIDER_RESPONSE: _("Waiting for provider response"),
    Job.JobStatus.HOLD: _("Waiting for provider response"),
    Job.JobStatus.PENDING_PROVIDER_CONFIRMATION: _("Waiting for provider response"),
    Job.JobStatus.PENDING_CLIENT_DECISION: _("Accepted"),
    Job.JobStatus.PENDING_CLIENT_CONFIRMATION: _("Accepted"),
    Job.JobStatus.ASSIGNED: _("Accepted"),
    Job.JobStatus.IN_PROGRESS: _("In progress"),
    Job.JobStatus.COMPLETED: _("Completed"),
    Job.JobStatus.CONFIRMED: _("Completed"),
    Job.JobStatus.CANCELLED: _("Cancelled"),
    Job.JobStatus.EXPIRED: _("Expired"),
}


def get_visible_job_status_label(job_or_status) -> str:
    if hasattr(job_or_status, "job_status"):
        status = getattr(job_or_status, "job_status", "") or ""
    else:
        status = str(job_or_status or "")
    return VISIBLE_JOB_STATUS_LABELS.get(status, status.replace("_", " ").title())


def _dispatch_job_event_push_after_commit(job_event_id: int) -> None:
    from notifications.services import dispatch_job_event_push_by_id

    dispatch_job_event_push_by_id(job_event_id)


@transaction.atomic
def create_job_event(
    *,
    job,
    event_type: str,
    actor_role: str = JobEvent.ActorRole.SYSTEM,
    payload: dict | None = None,
    provider_id: int | None = None,
    assignment_id: int | None = None,
    note: str = "",
    dedupe_seconds: int = 5,
    unique_per_job: bool = False,
    job_status: str | None = None,
    visible_status: str | None = None,
) -> JobEvent:
    payload_json = payload or {}
    if isinstance(job, Job):
        job_id = job.job_id
        resolved_status = job_status or getattr(job, "job_status", "") or ""
    else:
        job_id = int(job)
        resolved_status = job_status or ""
        if not resolved_status and not visible_status:
            resolved_status = (
                Job.objects.filter(pk=job_id).values_list("job_status", flat=True).first() or ""
            )

    resolved_visible_status = (
        visible_status
        if visible_status is not None
        else get_visible_job_status_label(resolved_status) if resolved_status else ""
    )

    base_qs = JobEvent.objects.select_for_update().filter(
        job_id=job_id,
        event_type=event_type,
    )
    if unique_per_job:
        existing = base_qs.order_by("-created_at").first()
        if existing:
            return existing

    now = timezone.now()
    since = now - timezone.timedelta(seconds=dedupe_seconds)
    qs = base_qs.filter(
        provider_id=provider_id,
        assignment_id=assignment_id,
        actor_role=actor_role,
        visible_status=resolved_visible_status,
        created_at__gte=since,
    )
    if note:
        qs = qs.filter(note=note)
    existing = qs.order_by("-created_at").first()
    if existing:
        return existing

    job_event = JobEvent.objects.create(
        job_id=job_id,
        event_type=event_type,
        provider_id=provider_id,
        assignment_id=assignment_id,
        visible_status=resolved_visible_status,
        actor_role=actor_role,
        payload_json=payload_json,
        note=note[:255],
        created_at=now,
    )
    transaction.on_commit(
        lambda created_job_event_id=job_event.pk: _dispatch_job_event_push_after_commit(
            created_job_event_id
        )
    )
    return job_event
