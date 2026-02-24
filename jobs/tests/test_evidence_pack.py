import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase
from django.utils import timezone

from clients.models import Client
from jobs.evidence import build_job_evidence_payload, write_job_evidence_json
from jobs.models import Job
from jobs.services import confirm_service_closed_by_client, start_service_by_provider
from jobs.services_extras import add_extra_line_for_job
from jobs.services_normal_client_confirm import confirm_normal_job_by_client
from providers.models import Provider
from service_type.models import ServiceType


class TestEvidencePack(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Evidence Pack Test",
            description="Evidence Pack Test",
        )
        self.client = Client.objects.create(
            first_name="Client",
            last_name="Evidence",
            phone_number="555-970-0001",
            email="client.evidence@test.local",
            country="Canada",
            province="AB",
            city="Calgary",
            postal_code="T1X1X1",
            address_line1="1 Client St",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Evidence",
            phone_number="555-970-0002",
            email="provider.evidence@test.local",
            province="AB",
            city="Calgary",
            postal_code="T1X1X1",
            address_line1="1 Provider St",
        )
        self.job = Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=2),
            service_type=self.service_type,
            client=self.client,
            selected_provider=self.provider,
            country="Canada",
            province="AB",
            city="Calgary",
            postal_code="T1X1X1",
            address_line1="1 Job St",
        )

    def test_write_json_contains_expected_sections(self):
        ok, *_ = confirm_normal_job_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertTrue(ok)

        add_extra_line_for_job(
            job_id=self.job.job_id,
            provider_id=self.provider.provider_id,
            description="Evidence extra",
            amount_cents=1000,
        )
        started = start_service_by_provider(job_id=self.job.job_id, provider_id=self.provider.provider_id)
        self.assertEqual(started, "started")
        closed = confirm_service_closed_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertEqual(closed, "closed_and_confirmed")

        payload = build_job_evidence_payload(self.job, run_id="EVID-1", source="finalize")
        self.assertIn("meta", payload)
        self.assertIn("job", payload)
        self.assertIn("ledger", payload)
        self.assertIn("tickets", payload)
        self.assertEqual(payload["meta"]["run_id"], "EVID-1")
        self.assertEqual(payload["meta"]["source"], "finalize")
        self.assertEqual(payload["job"]["status"], Job.JobStatus.CONFIRMED)
        self.assertTrue(payload["ledger"]["is_final"])
        self.assertIsNotNone(payload["tickets"]["provider"])
        self.assertIsNotNone(payload["tickets"]["client"])

        with TemporaryDirectory() as tmp_dir:
            out = write_job_evidence_json(
                self.job.job_id,
                out_dir=tmp_dir,
                run_id="EVID-1",
                source="finalize",
            )
            path = Path(out)
            self.assertTrue(path.exists())

            content = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("meta", content)
            self.assertIn("job", content)
            self.assertIn("ledger", content)
            self.assertIn("tickets", content)
