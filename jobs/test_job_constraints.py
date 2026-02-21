from datetime import timedelta

from django.db import IntegrityError
from django.test import TransactionTestCase
from django.utils import timezone

from jobs.models import Job
from service_type.models import ServiceType


class JobConstraintTests(TransactionTestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Constraint Test",
            description="Constraint test service type",
        )

    def _make_job(self, *, mode, scheduled_date):
        return Job.objects.create(
            job_mode=mode,
            scheduled_date=scheduled_date,
            job_status=Job.JobStatus.DRAFT,
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
        )

    def test_db_constraint_scheduled_requires_scheduled_date(self):
        job = self._make_job(mode=Job.JobMode.ON_DEMAND, scheduled_date=None)

        with self.assertRaises(IntegrityError):
            Job.objects.filter(pk=job.pk).update(
                job_mode=Job.JobMode.SCHEDULED,
                scheduled_date=None,
            )

    def test_db_constraint_on_demand_requires_null_scheduled_date(self):
        future_date = timezone.localdate() + timedelta(days=2)
        job = self._make_job(mode=Job.JobMode.SCHEDULED, scheduled_date=future_date)

        with self.assertRaises(IntegrityError):
            Job.objects.filter(pk=job.pk).update(
                job_mode=Job.JobMode.ON_DEMAND,
                scheduled_date=future_date,
            )
