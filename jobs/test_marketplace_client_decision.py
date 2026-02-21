from datetime import time, timedelta

from django.test import TestCase
from django.utils import timezone

from jobs.models import Job
from jobs.services import (
    MARKETPLACE_ACTION_CANCEL_JOB,
    MARKETPLACE_ACTION_EDIT_SCHEDULE_DATE,
    MARKETPLACE_ACTION_EXTEND_SEARCH_24H,
    MARKETPLACE_ACTION_SWITCH_TO_URGENT,
    MarketplaceDecisionConflict,
    apply_client_marketplace_decision,
)
from service_type.models import ServiceType


class MarketplaceClientDecisionTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Marketplace Decision Test",
            description="Marketplace decision test service type",
        )

    def _mk_job(self, *, status, scheduled_days=3):
        return Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=status,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=scheduled_days),
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

    def test_extend_search_24h_reactivates_waiting(self):
        job = self._mk_job(status=Job.JobStatus.PENDING_CLIENT_DECISION)

        result = apply_client_marketplace_decision(
            job_id=job.job_id,
            action=MARKETPLACE_ACTION_EXTEND_SEARCH_24H,
        )
        self.assertEqual(result, "extended_search")

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        self.assertIsNotNone(job.marketplace_search_started_at)
        self.assertIsNotNone(job.next_marketplace_alert_at)

    def test_edit_schedule_date_resets_window_and_sets_waiting(self):
        job = self._mk_job(status=Job.JobStatus.PENDING_CLIENT_DECISION)
        new_date = timezone.localdate() + timedelta(days=5)

        result = apply_client_marketplace_decision(
            job_id=job.job_id,
            action=MARKETPLACE_ACTION_EDIT_SCHEDULE_DATE,
            payload={"scheduled_date": new_date.isoformat()},
        )
        self.assertEqual(result, "schedule_updated")

        job.refresh_from_db()
        self.assertEqual(job.scheduled_date, new_date)
        self.assertEqual(job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        self.assertIsNotNone(job.marketplace_search_started_at)
        self.assertIsNotNone(job.next_marketplace_alert_at)

    def test_edit_schedule_date_rejects_less_than_24h(self):
        job = self._mk_job(status=Job.JobStatus.PENDING_CLIENT_DECISION)
        new_date = timezone.localdate()

        with self.assertRaises(MarketplaceDecisionConflict):
            apply_client_marketplace_decision(
                job_id=job.job_id,
                action=MARKETPLACE_ACTION_EDIT_SCHEDULE_DATE,
                payload={"scheduled_date": new_date.isoformat()},
            )

    def test_switch_to_urgent_cleans_marketplace_fields(self):
        job = self._mk_job(status=Job.JobStatus.PENDING_CLIENT_DECISION)

        result = apply_client_marketplace_decision(
            job_id=job.job_id,
            action=MARKETPLACE_ACTION_SWITCH_TO_URGENT,
        )
        self.assertEqual(result, "switched_to_urgent")

        job.refresh_from_db()
        self.assertEqual(job.job_mode, Job.JobMode.ON_DEMAND)
        self.assertIsNone(job.scheduled_date)
        self.assertEqual(job.job_status, Job.JobStatus.POSTED)
        self.assertIsNone(job.next_marketplace_alert_at)
        self.assertIsNone(job.marketplace_search_started_at)
        self.assertEqual(job.marketplace_attempts, 0)

    def test_cancel_job_from_pending_client_decision(self):
        job = self._mk_job(status=Job.JobStatus.PENDING_CLIENT_DECISION)

        result = apply_client_marketplace_decision(
            job_id=job.job_id,
            action=MARKETPLACE_ACTION_CANCEL_JOB,
        )
        self.assertEqual(result, "cancelled")

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.CANCELLED)
        self.assertIsNone(job.next_marketplace_alert_at)
