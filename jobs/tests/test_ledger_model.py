from django.db import IntegrityError, transaction
from django.test import TestCase

from jobs.models import Job, PlatformLedgerEntry
from service_type.models import ServiceType


class TestLedgerModel(TestCase):
    def test_one_to_one_no_duplicate_per_job(self):
        job = self._create_job()

        PlatformLedgerEntry.objects.create(job=job, gross_cents=100, tax_cents=15, fee_cents=0)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PlatformLedgerEntry.objects.create(job=job, gross_cents=200, tax_cents=30, fee_cents=0)

    def _create_job(self):
        service_type = ServiceType.objects.create(
            name="Ledger Model Test",
            description="Ledger Model Test",
        )
        return Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.DRAFT,
            service_type=service_type,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Job St",
        )
