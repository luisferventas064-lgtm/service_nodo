from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.services_scheduled_activation import activate_due_scheduled_jobs


class Command(BaseCommand):
    help = "Activate scheduled jobs once their scheduled date/time arrives."

    def handle(self, *args, **options):
        now = timezone.now()
        activated_job_ids = activate_due_scheduled_jobs(now=now)

        self.stdout.write(f"NOW: {now.isoformat()}")
        self.stdout.write(f"ACTIVATED SCHEDULED JOBS: {len(activated_job_ids)}")
        for job_id in activated_job_ids:
            self.stdout.write(f"ACTIVATED job_id={job_id}")
