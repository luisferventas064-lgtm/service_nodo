from django.core.exceptions import ValidationError
from django.test import TestCase

from jobs.models import Job
from service_type.models import ServiceType


class JobPricingSnapshotContractTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Pricing Snapshot Contract Test",
            description="Pricing snapshot contract test service type",
        )

    def _make_job(self) -> Job:
        return Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            scheduled_date=None,
            is_asap=True,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            service_type=self.service_type,
            quoted_base_price="100.00",
            quoted_base_price_cents=10_000,
            quoted_currency_code="CAD",
            quoted_currency="CAD",
            quoted_pricing_source="TestSnapshot",
            quoted_total_price_cents=10_000,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Snapshot St",
        )

    def test_snapshot_fields_are_immutable_once_captured(self):
        job = self._make_job()
        job.quoted_base_price_cents = 11_000

        with self.assertRaisesRegex(ValidationError, "Pricing snapshot is immutable"):
            job.save()

    def test_cannot_clear_snapshot_when_entering_active_lifecycle(self):
        job = self._make_job()
        job.job_status = Job.JobStatus.ASSIGNED
        job.quoted_base_price_cents = None

        with self.assertRaisesRegex(ValidationError, "Pricing snapshot is immutable"):
            job.save()
