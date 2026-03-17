import io
import zoneinfo
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from jobs.models import Job, JobEvent
from providers.models import Provider
from service_type.models import ServiceType


class TickScheduledActivationCommandTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Scheduled Activation Test",
            description="Scheduled activation command test",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Scheduled Activation",
            phone_number="5552220001",
            email="provider.scheduled.activation@test.local",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="1 Provider St",
        )

    def _make_job(self, *, scheduled_date, scheduled_start_time):
        return Job.objects.create(
            selected_provider=self.provider,
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.SCHEDULED_PENDING_ACTIVATION,
            is_asap=False,
            scheduled_date=scheduled_date,
            scheduled_start_time=scheduled_start_time,
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
        )

    def test_tick_scheduled_activation_promotes_due_job_once(self):
        now = timezone.now().astimezone(zoneinfo.ZoneInfo("America/Toronto")).replace(
            second=0,
            microsecond=0,
        )
        due_job = self._make_job(
            scheduled_date=now.date(),
            scheduled_start_time=(now - timedelta(minutes=5)).time(),
        )

        out = io.StringIO()
        call_command("tick_scheduled_activation", stdout=out)

        due_job.refresh_from_db()
        self.assertEqual(due_job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        self.assertEqual(
            list(due_job.events.order_by("created_at").values_list("event_type", flat=True)),
            [
                JobEvent.EventType.SCHEDULED_ACTIVATED,
                JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
            ],
        )
        self.assertIn("ACTIVATED SCHEDULED JOBS: 1", out.getvalue())

        out_second = io.StringIO()
        call_command("tick_scheduled_activation", stdout=out_second)

        due_job.refresh_from_db()
        self.assertEqual(due_job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        self.assertEqual(
            due_job.events.filter(event_type=JobEvent.EventType.SCHEDULED_ACTIVATED).count(),
            1,
        )
        self.assertEqual(
            due_job.events.filter(
                event_type=JobEvent.EventType.WAITING_PROVIDER_RESPONSE
            ).count(),
            1,
        )
        self.assertIn("ACTIVATED SCHEDULED JOBS: 0", out_second.getvalue())

    def test_tick_scheduled_activation_skips_future_job(self):
        now = timezone.now().astimezone(zoneinfo.ZoneInfo("America/Toronto")).replace(
            second=0,
            microsecond=0,
        )
        future_job = self._make_job(
            scheduled_date=(now + timedelta(days=1)).date(),
            scheduled_start_time=now.time(),
        )

        out = io.StringIO()
        call_command("tick_scheduled_activation", stdout=out)

        future_job.refresh_from_db()
        self.assertEqual(
            future_job.job_status,
            Job.JobStatus.SCHEDULED_PENDING_ACTIVATION,
        )
        self.assertFalse(future_job.events.exists())
        self.assertIn("ACTIVATED SCHEDULED JOBS: 0", out.getvalue())
