from django.db import transaction

from jobs.models import Job
from assignments.models import JobAssignment


def _activate_assignment_for_job(job: Job):
    # Desactiva cualquier assignment activo previo para este job
    JobAssignment.objects.filter(job=job, is_active=True).update(is_active=False)

    # Activa/crea el assignment para el provider seleccionado
    assignment, created = JobAssignment.objects.get_or_create(
        job=job,
        provider=job.selected_provider,
        defaults={"is_active": True},
    )

    if not created and not assignment.is_active:
        assignment.is_active = True
        assignment.save(update_fields=["is_active"])

    return assignment


@transaction.atomic
def confirm_normal_job_by_client(*, job_id: int, client_id: int):
    job = Job.objects.select_for_update().get(pk=job_id)

    if job.job_status != "pending_client_confirmation":
        return False, "INVALID_JOB_STATUS"

    if job.client_id != client_id:
        return False, "CLIENT_NOT_ALLOWED_FOR_THIS_JOB"

    job.job_status = "assigned"
    job.save(update_fields=["job_status", "updated_at"])

    assignment = _activate_assignment_for_job(job)

    return True, job, assignment