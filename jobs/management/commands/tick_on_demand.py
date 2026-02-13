from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from jobs.models import Job
from jobs.services import process_on_demand_job
from jobs.services_urgent_hold_expire import release_expired_holds


class Command(BaseCommand):
    help = "Processes due ON_DEMAND POSTED jobs (calls process_on_demand_job)."

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
            self.stdout.write(
                f"PROCESSING job_id={j.job_id} next_alert_at={j.next_alert_at} attempts={j.alert_attempts}"
            )

            # Keep each job isolated in its own transaction.
            with transaction.atomic():
                ok = process_on_demand_job(j)

            self.stdout.write(f"RESULT job_id={j.job_id}: {ok}")
