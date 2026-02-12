from django.utils import timezone
from datetime import timedelta


def schedule_next_alert(job):
    now = timezone.now()

    # Si ya existe una alerta futura, no hacer nada
    if job.next_alert_at and job.next_alert_at > now:
        return False

    job.next_alert_at = now + timedelta(minutes=2)
    job.alert_attempts += 1
    job.save(update_fields=["next_alert_at", "alert_attempts"])
    return True


def should_broadcast(job):
    return (
        job.job_mode == job.JobMode.ON_DEMAND
        and job.job_status == job.JobStatus.POSTED
    )


def process_on_demand_job(job):
    if not should_broadcast(job):
        return

    MAX_ALERT_ATTEMPTS = 10  # por ahora fijo

    if job.alert_attempts >= MAX_ALERT_ATTEMPTS:
        # deja de reintentar: lo marcamos como EXPIRED
        job.job_status = "expired"
        job.next_alert_at = None
        job.save(update_fields=["job_status", "next_alert_at"])
        return False

    scheduled = schedule_next_alert(job)
    return scheduled
