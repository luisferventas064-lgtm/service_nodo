from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from jobs.models import Job, PlatformLedgerEntry
from jobs.services import confirm_service_closed_by_client, start_service_by_provider
from jobs.services_extras import add_extra_line_for_job
from jobs.services_normal_client_confirm import confirm_normal_job_by_client
from clients.models import Client
from providers.models import Provider
from service_type.models import ServiceType


class TestLedgerFinalizeOnClose(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Ledger Finalize Close Test",
            description="Ledger Finalize Close Test",
        )
        self.client = Client.objects.create(
            first_name="Client",
            last_name="Finalize",
            phone_number="555-940-0001",
            email="client.ledger.finalize@test.local",
            country="Canada",
            province="AB",
            city="Calgary",
            postal_code="T1X1X1",
            address_line1="1 Client St",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Finalize",
            phone_number="555-940-0002",
            email="provider.ledger.finalize@test.local",
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

    def test_close_job_creates_ledger_entry(self):
        ok, *_ = confirm_normal_job_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertTrue(ok)

        add_extra_line_for_job(
            job_id=self.job.job_id,
            provider_id=self.provider.provider_id,
            description="Extra close",
            amount_cents=1000,
        )

        started = start_service_by_provider(job_id=self.job.job_id, provider_id=self.provider.provider_id)
        self.assertEqual(started, "started")

        result = confirm_service_closed_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertEqual(result, "closed_and_confirmed")

        entry = PlatformLedgerEntry.objects.get(job_id=self.job.job_id)
        self.assertGreaterEqual(entry.gross_cents, 0)
        self.assertIn(
            entry.fee_payer,
            [
                PlatformLedgerEntry.FEE_PAYER_CLIENT,
                PlatformLedgerEntry.FEE_PAYER_PROVIDER,
                PlatformLedgerEntry.FEE_PAYER_SPLIT,
            ],
        )

        second = confirm_service_closed_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertEqual(second, "already_confirmed")
        self.assertEqual(PlatformLedgerEntry.objects.filter(job_id=self.job.job_id).count(), 1)
