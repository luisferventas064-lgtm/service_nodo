from django.db import transaction

from jobs.models import Job
from jobs.services_state_transitions import transition_job_status
from jobs.services_validation import validate_normal_job_provider


@transaction.atomic
def accept_normal_job_by_provider(*, job_id: int, provider_id: int):
    ok, payload = validate_normal_job_provider(job_id=job_id, provider_id=provider_id)
    if not ok:
        return False, payload  # error_code

    # lock del job para evitar doble update
    job = Job.objects.select_for_update().get(pk=job_id)

    # re-validación bajo lock
    if job.job_status != "pending_provider_confirmation":
        return False, "INVALID_JOB_STATUS"

    if job.selected_provider_id != provider_id:
        return False, "PROVIDER_NOT_ALLOWED_FOR_THIS_JOB"

    transition_job_status(
        job,
        Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
        actor="provider",
        reason="accept_normal_job_by_provider",
        allow_legacy=True,
    )

    from providers.services_metrics import increment_accepted

    increment_accepted(provider_id)

    return True, job
