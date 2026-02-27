from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.test import override_settings

from jobs.ledger import rebuild_platform_ledger_for_job
from jobs.models import Job, PlatformLedgerEntry
from service_type.models import ServiceType


class TestAutoEvidenceOnRebuild(TestCase):
    @override_settings(ALLOW_LEDGER_REBUILD=True)
    def test_rebuild_calls_evidence_writer(self):
        service_type = ServiceType.objects.create(
            name="Auto Evidence Rebuild Test",
            description="Auto Evidence Rebuild Test",
        )
        job = Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            service_type=service_type,
            country="Canada",
            province="AB",
            city="Calgary",
            postal_code="T1X1X1",
            address_line1="1 Job St",
        )
        PlatformLedgerEntry.objects.create(job=job, is_final=True)

        expected_out_dir = getattr(settings, "NODO_EVIDENCE_DIR", None)
        with patch("jobs.ledger.try_write_job_evidence_json", return_value=None) as write_mock:
            rebuilt = rebuild_platform_ledger_for_job(
                job.job_id,
                run_id="TEST_RUN",
                reason="fix",
            )

        self.assertEqual(rebuilt.rebuild_count, 1)
        self.assertTrue(rebuilt.is_final)
        write_mock.assert_called_once_with(
            job.job_id,
            out_dir=expected_out_dir,
            run_id="TEST_RUN",
            source="rebuild",
        )
