from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from assignments.models import JobAssignment
from jobs.models import Job, JobStatus
from jobs.services import confirm_service_closed_by_client
from notifications.services import send_auto_confirmation_email


TIMEOUT_HOURS = 72


class Command(BaseCommand):
    help = "Auto-confirm completed jobs after 72 hours"

    def handle(self, *args, **options):
        now = timezone.now()
        cutoff = now - timedelta(hours=TIMEOUT_HOURS)

        jobs = Job.objects.filter(job_status=JobStatus.COMPLETED)

        total_checked = 0
        total_confirmed = 0

        for job in jobs:
            assignment = (
                JobAssignment.objects.filter(job_id=job.job_id, is_active=True)
                .order_by("-assignment_id")
                .first()
            )

            if not assignment or not assignment.completed_at:
                continue

            dispute = getattr(job, "dispute", None)
            if dispute and dispute.status in (
                dispute.DisputeStatus.OPEN,
                dispute.DisputeStatus.UNDER_REVIEW,
            ):
                continue

            total_checked += 1

            if assignment.completed_at <= cutoff:
                try:
                    with transaction.atomic():
                        confirm_service_closed_by_client(
                            job_id=job.job_id,
                            client_id=job.client_id,
                            source="auto_timeout",
                        )
                    send_auto_confirmation_email(job)
                    total_confirmed += 1
                    self.stdout.write(f"Auto-confirmed job {job.job_id}")
                except Exception as exc:
                    self.stdout.write(
                        f"Error auto-confirming job {job.job_id}: {str(exc)}"
                    )

        self.stdout.write(
            f"Checked: {total_checked}, Auto-confirmed: {total_confirmed}"
        )
