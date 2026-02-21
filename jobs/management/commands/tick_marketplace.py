from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from jobs.models import Job
from jobs.services import (
    CLIENT_CONFIRMATION_TIMEOUT_MINUTES,
    MARKETPLACE_MIN_LEAD_HOURS,
    process_marketplace_client_confirmation_timeout,
    process_marketplace_job,
)


class Command(BaseCommand):
    help = "Marketplace tick: retries scheduled jobs every 3h, waves new providers, expires before service date."

    def handle(self, *args, **options):
        now = timezone.now()
        min_date = timezone.localdate() + timedelta(days=1)

        qs = (
            Job.objects.filter(
                job_mode=Job.JobMode.SCHEDULED,
                job_status__in=[
                    Job.JobStatus.POSTED,
                    Job.JobStatus.WAITING_PROVIDER_RESPONSE,
                ],
                scheduled_date__isnull=False,
                scheduled_date__gte=min_date,
            )
            .filter(
                Q(next_marketplace_alert_at__isnull=True)
                | Q(next_marketplace_alert_at__lte=now)
                | Q(marketplace_expires_at__isnull=False, marketplace_expires_at__lte=now)
            )
            .order_by("next_marketplace_alert_at", "job_id")[:200]
        )

        due_job_ids = list(qs.values_list("job_id", flat=True))
        self.stdout.write(f"NOW: {now.isoformat()}")
        self.stdout.write(f"MIN_LEAD_HOURS: {MARKETPLACE_MIN_LEAD_HOURS}")
        self.stdout.write(f"DUE MARKETPLACE JOBS: {len(due_job_ids)}")

        for job_id in due_job_ids:
            result, sent, skipped = process_marketplace_job(job_id)
            self.stdout.write(f"JOB {job_id} RESULT: {result} sent={sent} skipped={skipped}")

        confirm_timeout_qs = (
            Job.objects.filter(
                job_mode=Job.JobMode.SCHEDULED,
                job_status=Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
                client_confirmation_started_at__isnull=False,
                client_confirmation_started_at__lte=now - timedelta(minutes=CLIENT_CONFIRMATION_TIMEOUT_MINUTES),
            )
            .order_by("client_confirmation_started_at", "job_id")[:200]
        )
        confirm_timeout_ids = list(confirm_timeout_qs.values_list("job_id", flat=True))
        self.stdout.write(f"DUE CLIENT CONFIRMATION TIMEOUTS: {len(confirm_timeout_ids)}")

        for job_id in confirm_timeout_ids:
            result, updated = process_marketplace_client_confirmation_timeout(job_id, now=now)
            self.stdout.write(f"JOB {job_id} CLIENT_CONFIRM_TIMEOUT: {result} updated={updated}")
