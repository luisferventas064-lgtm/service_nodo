from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import BroadcastAttemptStatus, Job, JobBroadcastAttempt
from jobs.services import (
    rank_broadcast_candidates_for_job,
    process_on_demand_job,
    record_broadcast_attempt,
    select_broadcast_wave_candidates,
)
from jobs.services_waiting_timeout import expire_waiting_jobs
from jobs.services_urgent_hold_expire import release_expired_holds


class Command(BaseCommand):
    help = "Tick on-demand jobs and broadcast alerts (simulated)."

    def handle(self, *args, **options):
        released = release_expired_holds()
        now = timezone.now()
        expired_waiting_jobs = expire_waiting_jobs(now=now)

        qs = (
            Job.objects.filter(job_mode="on_demand", job_status="posted")
            .filter(next_alert_at__isnull=False, next_alert_at__lte=now)
            .order_by("next_alert_at")[:50]
        )

        self.stdout.write(f"NOW: {now.isoformat()}")
        self.stdout.write(f"RELEASED HOLDS: {released}")
        self.stdout.write(f"EXPIRED WAITING JOBS: {len(expired_waiting_jobs)}")
        self.stdout.write(f"DUE JOBS: {qs.count()}")

        for j in qs:
            self.stdout.write(f"PROCESSING job_id={j.job_id}")
            result = process_on_demand_job(j.job_id)
            self.stdout.write(f"JOB {j.job_id} RESULT: {result}")

            j.refresh_from_db()
            current_attempt_number = int(getattr(j, "alert_attempts", 1) or 1)
            ranked_candidates = rank_broadcast_candidates_for_job(
                j,
                limit=10,
                attempt_number=current_attempt_number,
            )
            already_attempted = set(
                JobBroadcastAttempt.objects.filter(job_id=j.job_id).values_list("provider_id", flat=True)
            )
            provider_ids = select_broadcast_wave_candidates(
                ranked_candidates,
                already_attempted=already_attempted,
                batch_size=10,
                attempt_number=current_attempt_number,
            )
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
