from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from clients.models import Client
from jobs.models import Job
from jobs.services import confirm_service_closed_by_client, start_service_by_provider
from jobs.services_extras import add_extra_line_for_job
from jobs.services_normal_client_confirm import confirm_normal_job_by_client
from providers.models import Provider
from service_type.models import ServiceType


class TestAutoEvidenceOnClose(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Auto Evidence Close Test",
            description="Auto Evidence Close Test",
        )
        self.client = Client.objects.create(
            first_name="Client",
            last_name="AutoEvidence",
            phone_number="555-980-0001",
            email="client.auto.evidence.close@test.local",
            country="Canada",
            province="AB",
            city="Calgary",
            postal_code="T1X1X1",
            address_line1="1 Client St",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="AutoEvidence",
            phone_number="555-980-0002",
            email="provider.auto.evidence.close@test.local",
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

    def test_close_calls_evidence_writer(self):
        ok, *_ = confirm_normal_job_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertTrue(ok)

        add_extra_line_for_job(
            job_id=self.job.job_id,
            provider_id=self.provider.provider_id,
            description="Auto evidence extra",
            amount_cents=1000,
        )
        started = start_service_by_provider(job_id=self.job.job_id, provider_id=self.provider.provider_id)
        self.assertEqual(started, "started")

        expected_out_dir = getattr(settings, "NODO_EVIDENCE_DIR", None)
        with patch("jobs.services.try_write_job_evidence_json", return_value=None) as write_mock:
            result = confirm_service_closed_by_client(job_id=self.job.job_id, client_id=self.client.client_id)

        self.assertEqual(result, "closed_and_confirmed")
        write_mock.assert_called_once()
        args, kwargs = write_mock.call_args
        self.assertEqual(args[0], self.job.job_id)
        self.assertEqual(kwargs["out_dir"], expected_out_dir)
        self.assertEqual(kwargs["source"], "finalize")
        self.assertTrue(kwargs["run_id"].startswith("AUTO_CLOSE_"))
        self.assertTrue(kwargs["run_id"].endswith(f"_job_{self.job.job_id}"))
