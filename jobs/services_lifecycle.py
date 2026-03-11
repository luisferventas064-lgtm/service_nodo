from django.db import transaction
from django.utils import timezone

from assignments.models import JobAssignment
from jobs.models import Job


@transaction.atomic
def accept_job_by_provider(job, provider):
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

    job.job_status = Job.JobStatus.ASSIGNED
    job.save(update_fields=["job_status"])
    provider.__class__.objects.filter(pk=provider.pk).update(last_job_assigned_at=assigned_at)

    from providers.services_metrics import increment_accepted

    increment_accepted(provider.provider_id)

    return assignment
