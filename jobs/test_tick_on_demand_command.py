import io
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from jobs.models import Job, JobBroadcastAttempt
from providers.models import Provider, ProviderServiceArea, ProviderServiceType
from service_type.models import ServiceType


class TickOnDemandCommandTest(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Tick Command Test",
            description="Tick command test service type",
        )

    def _make_provider(self, n: int) -> Provider:
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name=f"Provider{n}",
            contact_last_name="Test",
            phone_number=f"555-000-000{n}",
            email=f"provider{n}.tick@test.local",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1=f"{n} Provider St",
        )
        ProviderServiceType.objects.create(
            provider=provider,
            service_type=self.service_type,
            price_type="fixed",
            base_price="50.00",
            is_active=True,
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            is_active=True,
        )
        return provider

    def _make_due_job(self) -> Job:
        return Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            scheduled_date=None,
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
            next_alert_at=timezone.now() - timedelta(minutes=1),
            on_demand_tick_scheduled_at=None,
            on_demand_tick_dispatched_at=None,
        )

    @patch("jobs.services.default_schedule_fn", autospec=True)
    def test_tick_on_demand_command_is_idempotent_for_broadcast_attempts(self, _schedule_mock):
        p1 = self._make_provider(1)
        p2 = self._make_provider(2)
        job = self._make_due_job()

        self.assertEqual(JobBroadcastAttempt.objects.count(), 0)

        out1 = io.StringIO()
        call_command("tick_on_demand", stdout=out1)

        attempts_after_first = JobBroadcastAttempt.objects.filter(job=job).count()
        self.assertEqual(attempts_after_first, 2)

        statuses_first = list(
            JobBroadcastAttempt.objects.filter(job=job)
            .order_by("provider_id")
            .values_list("provider_id", "status")
        )
        self.assertEqual(statuses_first, [(p1.provider_id, "sent"), (p2.provider_id, "sent")])

        out2 = io.StringIO()
        call_command("tick_on_demand", stdout=out2)

        attempts_after_second = JobBroadcastAttempt.objects.filter(job=job).count()
        self.assertEqual(attempts_after_second, 2)

        job.refresh_from_db()
        self.assertIsNotNone(job.on_demand_tick_dispatched_at)

    @patch("jobs.services.default_schedule_fn", autospec=True)
    def test_second_run_does_not_create_more_attempts_even_after_retry_window(self, _schedule_mock):
        self._make_provider(1)
        self._make_provider(2)
        job = self._make_due_job()

        call_command("tick_on_demand")
        self.assertEqual(JobBroadcastAttempt.objects.filter(job=job).count(), 2)

        job.refresh_from_db()
        job.on_demand_tick_scheduled_at = timezone.now() - timedelta(minutes=10)
        job.on_demand_tick_dispatched_at = None
        job.next_alert_at = timezone.now() - timedelta(minutes=1)
        job.save(
            update_fields=[
                "on_demand_tick_scheduled_at",
                "on_demand_tick_dispatched_at",
                "next_alert_at",
            ]
        )

        out2 = io.StringIO()
        call_command("tick_on_demand", stdout=out2)
        self.assertIn("skipped=2", out2.getvalue().lower())
        self.assertEqual(JobBroadcastAttempt.objects.filter(job=job).count(), 2)
