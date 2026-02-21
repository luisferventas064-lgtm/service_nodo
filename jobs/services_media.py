from __future__ import annotations

from dataclasses import dataclass
from django.db import transaction

from jobs.models import Job, JobMedia
from assignments.models import JobAssignment


def _coerce_pk(value, field_name: str) -> int:
    if value is None:
        raise ValueError(f"{field_name} is required.")
    try:
        return int(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be an int-like value.")


@dataclass(frozen=True)
class AddJobMediaResult:
    job_id: str
    media_id: int
    phase: str
    uploaded_by: str
    media_type: str


@transaction.atomic
def add_job_media(
    *,
    job_pk: int | str,
    uploaded_by: str,   # "client" | "provider"
    media_type: str,    # "image" | "video"
    phase: str,         # "pre_service" | "in_progress" | "post_service" | "dispute"
    file_obj,
    caption: str = "",
    client_pk: int | str | None = None,
    provider_pk: int | str | None = None,
) -> AddJobMediaResult:
    """
    Create JobMedia with minimal rules.
    - Client: only pre_service
    - Provider: in_progress or post_service (strict by status)
    """

    job_pk_int = _coerce_pk(job_pk, "job_pk")
    job = Job.objects.select_for_update().select_related("client", "selected_provider").get(pk=job_pk_int)

    # --- Validate enums (early) ---
    if uploaded_by not in (JobMedia.UploadedBy.CLIENT, JobMedia.UploadedBy.PROVIDER):
        raise ValueError("uploaded_by must be 'client' or 'provider'.")

    if media_type not in (JobMedia.MediaType.IMAGE, JobMedia.MediaType.VIDEO):
        raise ValueError("media_type must be 'image' or 'video'.")

    if phase not in (
        JobMedia.Phase.PRE_SERVICE,
        JobMedia.Phase.IN_PROGRESS,
        JobMedia.Phase.POST_SERVICE,
        JobMedia.Phase.DISPUTE,
    ):
        raise ValueError("Invalid phase.")

    # --- Permissions & status rules ---
    if uploaded_by == JobMedia.UploadedBy.CLIENT:
        client_pk_int = _coerce_pk(client_pk, "client_pk")

        if job.client_id != client_pk_int:
            raise ValueError("Client is not owner of this job.")

        if phase != JobMedia.Phase.PRE_SERVICE:
            raise ValueError("Client can only upload pre_service media (for now).")

        allowed_status = {
            Job.JobStatus.DRAFT,
            Job.JobStatus.POSTED,
            Job.JobStatus.PENDING_PROVIDER_CONFIRMATION,
            Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            Job.JobStatus.ASSIGNED,
        }
        if job.job_status not in allowed_status:
            raise ValueError(f"Client cannot upload media in status={job.job_status}")

    else:  # provider
        provider_pk_int = _coerce_pk(provider_pk, "provider_pk")

        active_assignment = (
            JobAssignment.objects
            .select_for_update()
            .filter(job_id=job.pk, is_active=True)
            .first()
        )
        if not active_assignment or active_assignment.provider_id != provider_pk_int:
            raise ValueError("Provider is not the active assigned provider for this job.")

        if job.selected_provider_id and job.selected_provider_id != provider_pk_int:
            raise ValueError("Provider is not the selected provider for this job.")

        if phase == JobMedia.Phase.IN_PROGRESS:
            allowed_status = {Job.JobStatus.ASSIGNED, Job.JobStatus.IN_PROGRESS}
            if job.job_status not in allowed_status:
                raise ValueError(f"Provider cannot upload in_progress media in status={job.job_status}")

        elif phase == JobMedia.Phase.POST_SERVICE:
            allowed_status = {Job.JobStatus.COMPLETED}
            if job.job_status not in allowed_status:
                raise ValueError(f"Provider cannot upload post_service media in status={job.job_status}")

        else:
            raise ValueError("Provider can only upload in_progress or post_service media (for now).")

    media = JobMedia.objects.create(
        job=job,
        uploaded_by=uploaded_by,
        media_type=media_type,
        phase=phase,
        file=file_obj,
        caption=caption or "",
    )

    return AddJobMediaResult(
        job_id=str(job.pk),
        media_id=media.media_id,
        phase=media.phase,
        uploaded_by=media.uploaded_by,
        media_type=media.media_type,
    )