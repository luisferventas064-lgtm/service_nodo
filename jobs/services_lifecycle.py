from django.db import transaction
from django.utils import timezone

from jobs.events import create_job_event
from assignments.models import JobAssignment
from jobs.models import Job, JobEvent
from jobs.services_state_transitions import transition_job_status


@transaction.atomic
def accept_job_by_provider(job, provider):
    if job.job_mode == Job.JobMode.SCHEDULED:
        raise ValueError("Scheduled marketplace accept must use accept_marketplace_offer().")

    if job.job_status != Job.JobStatus.WAITING_PROVIDER_RESPONSE:
        raise ValueError("Job not eligible for acceptance.")

    job.require_pricing_snapshot()

    if JobAssignment.objects.filter(job=job, is_active=True).exists():
        raise ValueError("Job already assigned.")

    assigned_at = timezone.now()
    assignment = JobAssignment.objects.create(
        job=job,
        provider=provider,
        assigned_at=assigned_at,
        is_active=True,
    )

    transition_job_status(
        job,
        Job.JobStatus.ASSIGNED,
        actor=JobEvent.ActorRole.PROVIDER,
        reason="accept_job_by_provider",
    )
    provider.__class__.objects.filter(pk=provider.pk).update(last_job_assigned_at=assigned_at)
    create_job_event(
        job=job,
        event_type=JobEvent.EventType.JOB_ACCEPTED,
        actor_role=JobEvent.ActorRole.PROVIDER,
        provider_id=provider.provider_id,
        assignment_id=assignment.assignment_id,
        payload={"source": "accept_job_by_provider"},
        unique_per_job=True,
    )

    from providers.services_metrics import increment_accepted

    increment_accepted(provider.provider_id)

    return assignment
