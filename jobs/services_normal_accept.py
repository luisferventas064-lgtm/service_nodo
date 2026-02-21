from django.db import transaction

from jobs.models import Job
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

    job.job_status = "pending_client_confirmation"
    job.save(update_fields=["job_status", "updated_at"])

    return True, job