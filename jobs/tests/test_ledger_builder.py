from django.test import TestCase

from jobs.ledger import upsert_platform_ledger_entry
from jobs.models import Job
from service_type.models import ServiceType


class TestLedgerBuilder(TestCase):
    def test_upsert_creates_and_is_idempotent(self):
        job = self._create_job()

        e1 = upsert_platform_ledger_entry(job.job_id)
        e2 = upsert_platform_ledger_entry(job.job_id)

        self.assertEqual(e1.pk, e2.pk)
        self.assertEqual(e2.gross_cents, 0)
        self.assertEqual(e2.tax_cents, 0)
        self.assertEqual(e2.fee_cents, 0)

    def _create_job(self):
        service_type = ServiceType.objects.create(
            name="Ledger Builder Test",
            description="Ledger Builder Test",
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
