from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import BroadcastAttemptStatus, Job
from jobs.services import (
    get_broadcast_candidates_for_job,
    process_on_demand_job,
    record_broadcast_attempt,
)
from jobs.services_urgent_hold_expire import release_expired_holds


class Command(BaseCommand):
    help = "Tick on-demand jobs and broadcast alerts (simulated)."

    def handle(self, *args, **options):
        released = release_expired_holds()
        now = timezone.now()

        qs = (
            Job.objects.filter(job_mode="on_demand", job_status="posted")
            .filter(next_alert_at__isnull=False, next_alert_at__lte=now)
            .order_by("next_alert_at")[:50]
        )

        self.stdout.write(f"NOW: {now.isoformat()}")
        self.stdout.write(f"RELEASED HOLDS: {released}")
        self.stdout.write(f"DUE JOBS: {qs.count()}")

        for j in qs:
            self.stdout.write(f"PROCESSING job_id={j.job_id}")
            result = process_on_demand_job(j.job_id)
            self.stdout.write(f"JOB {j.job_id} RESULT: {result}")

            provider_ids = get_broadcast_candidates_for_job(j, limit=10)
            sent = skipped = 0
            for pid in provider_ids:
                created = record_broadcast_attempt(
                    job_id=j.job_id,
                    provider_id=pid,
                    status=BroadcastAttemptStatus.SENT,
                    detail="simulated",
                )
                if created:
                    sent += 1
                else:
                    skipped += 1

            self.stdout.write(
                f"JOB {j.job_id} BROADCAST: sent={sent} skipped={skipped} candidates={len(provider_ids)}"
            )
