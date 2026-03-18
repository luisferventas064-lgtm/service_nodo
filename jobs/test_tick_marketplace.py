from decimal import Decimal
from datetime import time, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from jobs.models import Job, JobBroadcastAttempt, JobLocation, JobProviderExclusion
from jobs.services import process_marketplace_job
from providers.models import Provider, ProviderLocation, ProviderService, ProviderServiceArea
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
            availability_mode="manual",
            is_available_now=True,
            accepts_urgent=True,
            accepts_scheduled=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=self.service_type,
            custom_name="Marketplace Tick Service",
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

    @patch("jobs.services.MARKETPLACE_BATCH_SIZE", 2)
    def test_marketplace_wave_excludes_provider_who_already_declined_same_job(self):
        declined_provider = self._make_provider(1)
        eligible_provider = self._make_provider(2)
        job = self._make_scheduled_job()
        JobProviderExclusion.objects.create(
            job=job,
            provider=declined_provider,
            reason=JobProviderExclusion.Reason.DECLINED,
        )

        result = process_marketplace_job(job.job_id)

        self.assertEqual(result[0], "dispatched_wave")
        self.assertEqual(result[1], 1)
        self.assertEqual(
            list(
                JobBroadcastAttempt.objects.filter(job=job).values_list(
                    "provider_id",
                    flat=True,
                )
            ),
            [eligible_provider.provider_id],
        )

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

    @patch("jobs.services.MARKETPLACE_BATCH_SIZE", 4)
    def test_marketplace_adaptive_wave_sends_only_top_tier_candidates_first(self):
        top_provider = self._make_provider(1)
        strong_provider = self._make_provider(2)
        weak_provider_one = self._make_provider(3)
        weak_provider_two = self._make_provider(4)

        top_provider.avg_rating = Decimal("5.00")
        top_provider.last_job_assigned_at = timezone.now() - timedelta(hours=4)
        top_provider.save(update_fields=["avg_rating", "last_job_assigned_at"])

        strong_provider.avg_rating = Decimal("4.90")
        strong_provider.last_job_assigned_at = timezone.now() - timedelta(hours=4)
        strong_provider.save(update_fields=["avg_rating", "last_job_assigned_at"])

        weak_provider_one.avg_rating = Decimal("1.00")
        weak_provider_one.last_job_assigned_at = timezone.now()
        weak_provider_one.save(update_fields=["avg_rating", "last_job_assigned_at"])

        weak_provider_two.avg_rating = Decimal("1.00")
        weak_provider_two.last_job_assigned_at = timezone.now()
        weak_provider_two.save(update_fields=["avg_rating", "last_job_assigned_at"])

        job = self._make_scheduled_job()
        JobLocation.objects.create(
            job=job,
            latitude=Decimal("45.560100"),
            longitude=Decimal("-73.712400"),
            postal_code="H7N1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )
        ProviderLocation.objects.create(
            provider=top_provider,
            latitude=Decimal("45.560100"),
            longitude=Decimal("-73.712400"),
            postal_code="H7N1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )
        ProviderLocation.objects.create(
            provider=strong_provider,
            latitude=Decimal("45.561000"),
            longitude=Decimal("-73.713000"),
            postal_code="H7N1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )
        ProviderLocation.objects.create(
            provider=weak_provider_one,
            latitude=Decimal("45.501700"),
            longitude=Decimal("-73.567300"),
            postal_code="H1A1A1",
            city="Montreal",
            province="QC",
            country="Canada",
        )
        ProviderLocation.objects.create(
            provider=weak_provider_two,
            latitude=Decimal("45.495000"),
            longitude=Decimal("-73.560000"),
            postal_code="H1A1A1",
            city="Montreal",
            province="QC",
            country="Canada",
        )

        result = process_marketplace_job(job.job_id)

        self.assertEqual(result[0], "dispatched_wave")
        self.assertEqual(result[1], 2)
        attempt_provider_ids = list(
            JobBroadcastAttempt.objects.filter(job=job)
            .order_by("provider_id")
            .values_list("provider_id", flat=True)
        )
        self.assertEqual(
            set(attempt_provider_ids),
            {top_provider.provider_id, strong_provider.provider_id},
        )
