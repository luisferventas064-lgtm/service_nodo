from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from jobs.models import Job, KIND_ON_DEMAND, KIND_SCHEDULED
from service_type.models import ServiceType


class JobNormalizationTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Normalization Test",
            description="Normalization test service type",
        )

    def _mk(self, **overrides):
        data = {
            "job_mode": KIND_ON_DEMAND,
            "scheduled_date": None,
            "job_status": Job.JobStatus.DRAFT,
            "service_type": self.service_type,
            "province": "QC",
            "city": "Laval",
            "postal_code": "H7N1A1",
            "address_line1": "123 Main St",
        }
        data.update(overrides)
        return Job(**data)

    def test_scheduled_requires_future_scheduled_date(self):
        job = self._mk(job_mode=KIND_SCHEDULED, scheduled_date=None)
        with self.assertRaises(ValidationError):
            job.full_clean()

        future_date = timezone.localdate() + timedelta(days=2)
        job = self._mk(job_mode=KIND_SCHEDULED, scheduled_date=future_date)
        job.full_clean()

    def test_on_demand_clears_scheduled_date(self):
        future_date = timezone.localdate() + timedelta(days=2)
        job = self._mk(job_mode=KIND_ON_DEMAND, scheduled_date=future_date)
        job.full_clean()
        self.assertIsNone(job.scheduled_date)

    def test_past_or_today_scheduled_date_normalizes_to_on_demand(self):
        today = timezone.localdate()
        job = self._mk(job_mode=KIND_SCHEDULED, scheduled_date=today)
        job.full_clean()
        self.assertEqual(job.job_mode, KIND_ON_DEMAND)
        self.assertIsNone(job.scheduled_date)
