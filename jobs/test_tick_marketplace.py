from datetime import time, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from jobs.models import Job, JobBroadcastAttempt
from jobs.services import process_marketplace_job
from providers.models import Provider, ProviderServiceArea, ProviderServiceType
from service_type.models import ServiceType


class MarketplaceTickTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Marketplace Tick Test",
            description="Marketplace tick service type",
        )

    def _make_provider(self, n: int) -> Provider:
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name=f"Provider{n}",
            contact_last_name="Market",
            phone_number=f"555-111-000{n}",
            email=f"provider{n}.market@test.local",
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

    def _make_scheduled_job(self) -> Job:
        return Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.POSTED,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=3),
            scheduled_start_time=time(hour=12, minute=0),
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
        )

    @patch("jobs.services.MARKETPLACE_BATCH_SIZE", 2)
    def test_marketplace_waves_do_not_repeat_providers(self):
        for i in range(1, 6):
            self._make_provider(i)
        job = self._make_scheduled_job()

        r1 = process_marketplace_job(job.job_id)
        self.assertEqual(r1[0], "dispatched_wave")
        self.assertEqual(r1[1], 2)
        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        self.assertIsNotNone(job.marketplace_search_started_at)

        job.next_marketplace_alert_at = timezone.now() - timedelta(minutes=1)
        job.save(update_fields=["next_marketplace_alert_at"])

        r2 = process_marketplace_job(job.job_id)
        self.assertEqual(r2[0], "dispatched_wave")
        self.assertEqual(r2[1], 2)

        provider_ids = list(
            JobBroadcastAttempt.objects.filter(job=job)
            .order_by("provider_id")
            .values_list("provider_id", flat=True)
        )
        self.assertEqual(len(provider_ids), 4)
        self.assertEqual(len(set(provider_ids)), 4)

    def test_marketplace_expires_when_expiration_time_passed(self):
        job = self._make_scheduled_job()
        job.marketplace_expires_at = timezone.now() - timedelta(minutes=1)
        job.next_marketplace_alert_at = timezone.now() - timedelta(minutes=1)
        job.save(update_fields=["marketplace_expires_at", "next_marketplace_alert_at"])

        result = process_marketplace_job(job.job_id)
        self.assertEqual(result[0], "expired_no_provider")

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.EXPIRED)
        self.assertIsNone(job.next_marketplace_alert_at)

    @patch("jobs.services.MARKETPLACE_BATCH_SIZE", 2)
    def test_due_with_no_new_candidates_increments_attempts(self):
        self._make_provider(1)
        self._make_provider(2)
        job = self._make_scheduled_job()

        r1 = process_marketplace_job(job.job_id)
        self.assertEqual(r1[0], "dispatched_wave")

        job.refresh_from_db()
        job.next_marketplace_alert_at = timezone.now() - timedelta(minutes=1)
        job.save(update_fields=["next_marketplace_alert_at"])

        r2 = process_marketplace_job(job.job_id)
        self.assertEqual(r2[0], "due_no_new_candidates")

        job.refresh_from_db()
        self.assertEqual(job.marketplace_attempts, 2)

    def test_search_timeout_moves_to_pending_client_decision(self):
        self._make_provider(1)
        job = self._make_scheduled_job()

        r1 = process_marketplace_job(job.job_id)
        self.assertEqual(r1[0], "dispatched_wave")

        job.refresh_from_db()
        job.marketplace_search_started_at = timezone.now() - timedelta(hours=24, minutes=1)
        job.next_marketplace_alert_at = timezone.now() - timedelta(minutes=1)
        job.job_status = Job.JobStatus.WAITING_PROVIDER_RESPONSE
        job.save(
            update_fields=[
                "marketplace_search_started_at",
                "next_marketplace_alert_at",
                "job_status",
            ]
        )

        r2 = process_marketplace_job(job.job_id)
        self.assertEqual(r2[0], "pending_client_decision_timeout_24h")

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.PENDING_CLIENT_DECISION)
        self.assertIsNone(job.next_marketplace_alert_at)
