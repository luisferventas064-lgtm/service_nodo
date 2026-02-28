from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from assignments.models import JobAssignment
from jobs.models import Job
from providers.models import ProviderReview
from providers.services_metrics import recalc_avg_rating


def _resolve_provider_id_for_job(job: Job) -> int | None:
    active_assignment = JobAssignment.objects.filter(
        job_id=job.job_id,
        is_active=True,
    ).first()
    if active_assignment:
        return active_assignment.provider_id
    return job.selected_provider_id


@transaction.atomic
def create_provider_review(
    *,
    job_id: int,
    client,
    rating: int,
    comment: str = "",
) -> ProviderReview:
    job = Job.objects.select_for_update().get(pk=job_id)

    if job.job_status != Job.JobStatus.CONFIRMED:
        raise ValidationError("Job must be confirmed to create a review.")

    if not job.client_id:
        raise ValidationError("Job has no client assigned.")

    if not client or job.client_id != getattr(client, "client_id", None):
        raise ValidationError("Client does not match job.")

    if ProviderReview.objects.filter(job_id=job_id).exists():
        raise ValidationError("Review already exists for this job.")

    provider_id = _resolve_provider_id_for_job(job)
    if not provider_id:
        raise ValidationError("Provider could not be resolved for job.")

    review = ProviderReview(
        job=job,
        provider_id=provider_id,
        client_id=job.client_id,
        rating=rating,
        comment=comment or "",
    )
    review.save()

    recalc_avg_rating(provider_id)
    return review
