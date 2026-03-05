from datetime import time, timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from jobs.models import Job, JobBroadcastAttempt
from jobs.services import (
    MARKETPLACE_ACTION_SWITCH_TO_URGENT,
    apply_client_marketplace_decision,
)
from providers.models import Provider, ProviderService, ProviderServiceArea
from service_type.models import ServiceType


class SwitchToUrgentTickCycleTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Switch To Urgent Tick Test",
            description="Switch to urgent tick cycle test service type",
        )

    def _make_provider(self, n: int) -> Provider:
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name=f"Provider{n}",
            contact_last_name="Switch",
            phone_number=f"555-222-000{n}",
            email=f"provider{n}.switch.tick@test.local",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1=f"{n} Provider St",
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=self.service_type,
            custom_name="Switch Tick Service",
            description="",
            billing_unit="fixed",
            price_cents=5000,
            is_active=True,
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            is_active=True,
        )
        return provider

    def _make_marketplace_job(self) -> Job:
        return Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.PENDING_CLIENT_DECISION,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=3),
            scheduled_start_time=time(hour=12, minute=0),
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
            marketplace_search_started_at=timezone.now() - timedelta(hours=25),
            next_marketplace_alert_at=timezone.now() - timedelta(minutes=1),
            marketplace_attempts=3,
        )

    def test_switch_to_urgent_enters_tick_cycle(self):
        self._make_provider(1)
        job = self._make_marketplace_job()

        apply_client_marketplace_decision(
            job_id=job.job_id,
            action=MARKETPLACE_ACTION_SWITCH_TO_URGENT,
        )

        job.refresh_from_db()

        self.assertEqual(job.job_mode, Job.JobMode.ON_DEMAND)
        self.assertEqual(job.job_status, Job.JobStatus.POSTED)
        self.assertIsNotNone(job.next_alert_at)

        call_command("tick_on_demand")

        self.assertTrue(JobBroadcastAttempt.objects.filter(job=job).exists())
