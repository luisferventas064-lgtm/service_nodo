import io
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from clients.models import Client
from jobs.models import Job, JobDispute, JobEvent
from jobs.services import complete_service_by_provider, start_service_by_provider
from jobs.services_normal_client_confirm import confirm_normal_job_by_client
from providers.models import Provider
from service_type.models import ServiceType


class AutoConfirmCompletedJobsCommandTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Auto Confirm Completed",
            description="Auto confirm completed command test",
        )
        self.client = Client.objects.create(
            first_name="Client",
            last_name="AutoConfirm",
            phone_number="555-777-0001",
            email="client.auto.confirm.completed@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="AutoConfirm",
            phone_number="555-777-0002",
            email="provider.auto.confirm.completed@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="2 Provider St",
        )

    def _make_completed_job(self, *, completed_at):
        job = Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=2),
            service_type=self.service_type,
            client=self.client,
            selected_provider=self.provider,
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="3 Job St",
        )

        ok, *_ = confirm_normal_job_by_client(
            job_id=job.job_id,
            client_id=self.client.client_id,
        )
        self.assertTrue(ok)

        started = start_service_by_provider(
            job_id=job.job_id,
            provider_id=self.provider.provider_id,
        )
        self.assertEqual(started, "started")

        completed = complete_service_by_provider(
            job_id=job.job_id,
            provider_id=self.provider.provider_id,
        )
        self.assertEqual(completed, "completed")

        assignment = job.assignments.get(is_active=True)
        assignment.completed_at = completed_at
        assignment.save(update_fields=["completed_at", "updated_at"])

        return job

    @patch("jobs.management.commands.auto_confirm_completed_jobs.send_auto_confirmation_email")
    def test_auto_confirms_jobs_completed_more_than_72_hours_ago(self, email_mock):
        job = self._make_completed_job(
            completed_at=timezone.now() - timedelta(hours=73),
        )

        out = io.StringIO()
        call_command("auto_confirm_completed_jobs", stdout=out)

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.CONFIRMED)
        event = job.events.filter(event_type=JobEvent.EventType.CLIENT_CONFIRMED).latest("created_at")
        self.assertEqual(event.note, "auto_timeout_72h")
        email_mock.assert_called_once_with(job)
        self.assertIn(f"Auto-confirmed job {job.job_id}", out.getvalue())
        self.assertIn("Checked: 1, Auto-confirmed: 1", out.getvalue())

    @patch("jobs.management.commands.auto_confirm_completed_jobs.send_auto_confirmation_email")
    def test_keeps_recently_completed_jobs_open(self, email_mock):
        job = self._make_completed_job(
            completed_at=timezone.now() - timedelta(hours=24),
        )

        out = io.StringIO()
        call_command("auto_confirm_completed_jobs", stdout=out)

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.COMPLETED)
        email_mock.assert_not_called()
        self.assertIn("Checked: 1, Auto-confirmed: 0", out.getvalue())

    @patch("jobs.management.commands.auto_confirm_completed_jobs.send_auto_confirmation_email")
    def test_skips_jobs_with_under_review_disputes(self, email_mock):
        job = self._make_completed_job(
            completed_at=timezone.now() - timedelta(hours=73),
        )
        JobDispute.objects.create(
            job=job,
            client_id=self.client.client_id,
            provider_id=self.provider.provider_id,
            reason="Client disputes completion",
            status=JobDispute.DisputeStatus.UNDER_REVIEW,
        )

        out = io.StringIO()
        call_command("auto_confirm_completed_jobs", stdout=out)

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.COMPLETED)
        self.assertFalse(
            job.events.filter(event_type=JobEvent.EventType.CLIENT_CONFIRMED).exists()
        )
        email_mock.assert_not_called()
        self.assertIn("Checked: 0, Auto-confirmed: 0", out.getvalue())
