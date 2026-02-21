from jobs.models import Job
from providers.models import Provider


def validate_normal_job_provider(job_id: int, provider_id: int):
    """
    Valida:
    - Job existe
    - Provider existe
    - Job está en estado correcto (pending_provider_confirmation)
    - Provider corresponde al seleccionado en el job
    """

    # 1) Job existe
    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return False, "JOB_NOT_FOUND"

    # 2) Provider existe
    try:
        provider = Provider.objects.get(pk=provider_id)
    except Provider.DoesNotExist:
        return False, "PROVIDER_NOT_FOUND"

    # 3) Estado correcto
    if job.job_status != "pending_provider_confirmation":
        return False, "INVALID_JOB_STATUS"

    # 4) Provider coincide con el seleccionado
    if job.selected_provider_id != provider_id:
        return False, "PROVIDER_NOT_ALLOWED_FOR_THIS_JOB"

    return True, job
