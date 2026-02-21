from django.test import TestCase

from jobs.models import BroadcastAttemptStatus, Job, JobBroadcastAttempt
from jobs.services import record_broadcast_attempt
from providers.models import Provider
from service_type.models import ServiceType


class BroadcastAttemptTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Broadcast Attempt Test",
            description="Broadcast attempt service type",
        )

    def _create_job(self):
        return Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            scheduled_date=None,
            is_asap=True,
            job_status=Job.JobStatus.POSTED,
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
        )

    def _create_provider(self):
        return Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Test",
            contact_last_name="Provider",
            phone_number="555-000-0000",
            email="provider.broadcast@test.local",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="100 Provider St",
        )

    def test_record_attempt_is_unique_per_job_provider(self):
        j = self._create_job()
        p = self._create_provider()

        ok1 = record_broadcast_attempt(
            job_id=j.job_id,
            provider_id=p.provider_id,
            status=BroadcastAttemptStatus.SENT,
        )
        ok2 = record_broadcast_attempt(
            job_id=j.job_id,
            provider_id=p.provider_id,
            status=BroadcastAttemptStatus.SENT,
        )

        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(JobBroadcastAttempt.objects.count(), 1)
