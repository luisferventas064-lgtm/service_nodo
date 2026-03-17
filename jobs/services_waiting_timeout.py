from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from jobs.events import create_job_event
from jobs.models import Job, JobEvent
from jobs.services_state_transitions import transition_job_status

WAITING_PROVIDER_RESPONSE_TIMEOUT_MINUTES = 5


@transaction.atomic
def expire_waiting_jobs(*, now=None, timeout_minutes: int = WAITING_PROVIDER_RESPONSE_TIMEOUT_MINUTES) -> list[int]:
    now = now or timezone.now()
    cutoff = now - timedelta(minutes=timeout_minutes)
    expired_job_ids: list[int] = []

    waiting_jobs = list(
        Job.objects.select_for_update()
        .filter(
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            selected_provider_id__isnull=False,
            created_at__lt=cutoff,
        )
        .order_by("job_id")
    )

    for job in waiting_jobs:
        transition_job_status(
            job,
            Job.JobStatus.EXPIRED,
            actor=JobEvent.ActorRole.SYSTEM,
            reason="waiting_timeout",
        )
        job.cancelled_by = Job.CancellationActor.SYSTEM
        job.cancel_reason = Job.CancelReason.AUTO_TIMEOUT
        job.next_alert_at = None
        job.save(
            update_fields=[
                "cancelled_by",
                "cancel_reason",
                "next_alert_at",
                "updated_at",
            ]
        )
        create_job_event(
            job=job,
            event_type=JobEvent.EventType.JOB_EXPIRED,
            actor_role=JobEvent.ActorRole.SYSTEM,
            provider_id=job.selected_provider_id,
            payload={"reason": "timeout"},
            job_status=Job.JobStatus.EXPIRED,
            note="waiting provider response expired after timeout",
        )
        expired_job_ids.append(job.job_id)

    return expired_job_ids
