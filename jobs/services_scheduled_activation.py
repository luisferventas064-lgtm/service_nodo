from __future__ import annotations

from datetime import datetime, time

from django.db import transaction
from django.utils import timezone

from jobs.events import create_job_event
from jobs.models import Job, JobEvent
from jobs.services_state_transitions import transition_job_status


def _scheduled_for(job: Job):
    if not job.scheduled_date:
        return None

    scheduled_time = job.scheduled_start_time or time.min
    return timezone.make_aware(
        datetime.combine(job.scheduled_date, scheduled_time),
        job.get_job_timezone(),
    )


def activate_due_scheduled_jobs(*, now=None, limit: int = 200) -> list[int]:
    now = now or timezone.now()
    candidate_ids = list(
        Job.objects.filter(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.SCHEDULED_PENDING_ACTIVATION,
            scheduled_date__isnull=False,
        )
        .order_by("scheduled_date", "scheduled_start_time", "job_id")
        .values_list("job_id", flat=True)[:limit]
    )

    activated_job_ids = []

    for job_id in candidate_ids:
        with transaction.atomic():
            job = (
                Job.objects.select_for_update()
                .select_related("selected_provider")
                .filter(pk=job_id)
                .first()
            )
            if job is None or job.job_status != Job.JobStatus.SCHEDULED_PENDING_ACTIVATION:
                continue

            scheduled_for = _scheduled_for(job)
            if scheduled_for is None or scheduled_for > now:
                continue

            transition_job_status(
                job,
                Job.JobStatus.WAITING_PROVIDER_RESPONSE,
                actor=JobEvent.ActorRole.SYSTEM,
                reason="tick_scheduled_activation",
            )

            payload = {
                "source": "tick_scheduled_activation",
                "scheduled_for": scheduled_for.isoformat(),
            }
            create_job_event(
                job=job,
                event_type=JobEvent.EventType.SCHEDULED_ACTIVATED,
                actor_role=JobEvent.ActorRole.SYSTEM,
                payload=payload,
                provider_id=getattr(job.selected_provider, "provider_id", None),
                unique_per_job=True,
                job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
                note="scheduled job activated",
            )
            create_job_event(
                job=job,
                event_type=JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
                actor_role=JobEvent.ActorRole.SYSTEM,
                payload=payload,
                provider_id=getattr(job.selected_provider, "provider_id", None),
                job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
                note="scheduled activation moved job into waiting provider response",
            )
            activated_job_ids.append(job.job_id)

    return activated_job_ids
