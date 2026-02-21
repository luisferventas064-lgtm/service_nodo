from datetime import time, timedelta

from django.test import TestCase
from django.utils import timezone

from jobs.models import BroadcastAttemptStatus, Job, JobBroadcastAttempt
from jobs.services import (
    MARKETPLACE_ACTION_EXTEND_SEARCH_24H,
    MarketplaceAcceptConflict,
    accept_marketplace_offer,
    apply_client_marketplace_decision,
)
from providers.models import Provider
from service_type.models import ServiceType


class MarketplaceProviderAcceptRaceTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Marketplace Accept Race Test",
            description="Marketplace provider accept race test service type",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Race",
            phone_number="555-777-0001",
            email="provider.race.accept@test.local",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="99 Provider St",
        )

    def _mk_job(self, *, status=Job.JobStatus.WAITING_PROVIDER_RESPONSE) -> Job:
        started_at = timezone.now() - timedelta(hours=1)
        return Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=status,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=3),
            scheduled_start_time=time(hour=12, minute=0),
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
            marketplace_search_started_at=started_at,
            next_marketplace_alert_at=timezone.now() + timedelta(hours=2),
        )

    def _mk_attempt(self, job: Job, *, created_at=None) -> JobBroadcastAttempt:
        attempt = JobBroadcastAttempt.objects.create(
            job_id=job.job_id,
            provider_id=self.provider.provider_id,
            status=BroadcastAttemptStatus.SENT,
            detail="race-test",
        )
        if created_at is not None:
            JobBroadcastAttempt.objects.filter(pk=attempt.pk).update(created_at=created_at)
            attempt.refresh_from_db()
        return attempt

    def test_accept_ok_current_window(self):
        job = self._mk_job()
        self._mk_attempt(job, created_at=timezone.now() - timedelta(minutes=5))

        result = accept_marketplace_offer(job_id=job.job_id, provider_id=self.provider.provider_id)
        self.assertEqual(result, "accepted_waiting_client")

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.PENDING_CLIENT_CONFIRMATION)
        self.assertEqual(job.selected_provider_id, self.provider.provider_id)
        self.assertIsNotNone(job.client_confirmation_started_at)
        self.assertIsNone(job.next_marketplace_alert_at)

    def test_accept_rejected_if_pending_client_decision(self):
        job = self._mk_job(status=Job.JobStatus.PENDING_CLIENT_DECISION)
        self._mk_attempt(job, created_at=timezone.now() - timedelta(minutes=5))

        with self.assertRaises(MarketplaceAcceptConflict):
            accept_marketplace_offer(job_id=job.job_id, provider_id=self.provider.provider_id)

    def test_accept_rejected_old_attempt_after_extend(self):
        job = self._mk_job()
        old_attempt_time = timezone.now() - timedelta(hours=2)
        self._mk_attempt(job, created_at=old_attempt_time)

        job.job_status = Job.JobStatus.PENDING_CLIENT_DECISION
        job.save(update_fields=["job_status"])
        apply_client_marketplace_decision(
            job_id=job.job_id,
            action=MARKETPLACE_ACTION_EXTEND_SEARCH_24H,
        )

        with self.assertRaises(MarketplaceAcceptConflict):
            accept_marketplace_offer(job_id=job.job_id, provider_id=self.provider.provider_id)

    def test_accept_rejected_after_timeout(self):
        job = self._mk_job()
        self._mk_attempt(job, created_at=timezone.now() - timedelta(hours=25))

        job.marketplace_search_started_at = timezone.now() - timedelta(hours=25)
        job.save(update_fields=["marketplace_search_started_at"])

        with self.assertRaises(MarketplaceAcceptConflict):
            accept_marketplace_offer(job_id=job.job_id, provider_id=self.provider.provider_id)

    def test_accept_idempotent_same_provider(self):
        job = self._mk_job()
        self._mk_attempt(job, created_at=timezone.now() - timedelta(minutes=5))

        accept_marketplace_offer(job_id=job.job_id, provider_id=self.provider.provider_id)
        res = accept_marketplace_offer(job_id=job.job_id, provider_id=self.provider.provider_id)
        self.assertEqual(res, "already_accepted_waiting_client")
