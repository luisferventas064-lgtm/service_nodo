from decimal import Decimal

from django.test import TestCase

from clients.models import Client, ClientTicket
from jobs.activity_financial_adapter import (
    ActivityFinancialAdapter,
    build_activity_financial_data_map,
)
from jobs.models import Job, PlatformLedgerEntry
from providers.models import Provider
from service_type.models import ServiceType


class ActivityFinancialAdapterTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(
            first_name="Adapter",
            last_name="Client",
            email="adapter.client@test.local",
            phone_number="+15145551301",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1 Adapter St",
        )
        self.provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Adapter Provider",
            contact_first_name="Adapter",
            contact_last_name="Provider",
            phone_number="+15145551302",
            email="adapter.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="2 Adapter St",
        )
        self.service_type = ServiceType.objects.create(
            name="Adapter Service",
            description="Adapter Service",
        )

    def test_client_adapter_uses_ticket_total_and_status(self):
        job = Job.objects.create(
            client=self.client_obj,
            selected_provider=self.provider,
            service_type=self.service_type,
            provider_service_name_snapshot="Adapter Offer",
            requested_total_snapshot=Decimal("45.00"),
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.ASSIGNED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="3 Adapter St",
        )
        ticket = ClientTicket.objects.create(
            client=self.client_obj,
            ref_type="job",
            ref_id=job.job_id,
            ticket_no="CT-ADAPTER-001",
            status=ClientTicket.Status.FINALIZED,
            total_cents=4_800,
        )

        data = ActivityFinancialAdapter(
            job,
            "client",
            client_ticket=ticket,
            job_financial=job.financial,
        ).build()

        self.assertEqual(data.total_charged_cents, 4_800)
        self.assertEqual(data.payment_status, ClientTicket.Status.FINALIZED)
        self.assertIsNone(data.gross_cents)

    def test_provider_adapter_uses_ledger_values(self):
        job = Job.objects.create(
            client=self.client_obj,
            selected_provider=self.provider,
            service_type=self.service_type,
            provider_service_name_snapshot="Adapter Provider Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="4 Adapter St",
        )
        ledger = PlatformLedgerEntry.objects.create(
            job=job,
            gross_cents=12_000,
            fee_cents=2_500,
            net_provider_cents=9_500,
            is_final=True,
        )

        data = ActivityFinancialAdapter(job, "provider", ledger=ledger).build()

        self.assertEqual(data.gross_cents, 12_000)
        self.assertEqual(data.platform_fee_cents, 2_500)
        self.assertEqual(data.provider_net_cents, 9_500)
        self.assertIsNone(data.total_charged_cents)

    def test_build_activity_financial_data_map_returns_empty_worker_payload(self):
        job = Job.objects.create(
            client=self.client_obj,
            selected_provider=self.provider,
            service_type=self.service_type,
            provider_service_name_snapshot="Adapter Worker Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="5 Adapter St",
        )

        data_map = build_activity_financial_data_map([job], "worker")

        self.assertIsNone(data_map[job.job_id].total_charged_cents)
        self.assertIsNone(data_map[job.job_id].gross_cents)
        self.assertIsNone(data_map[job.job_id].platform_fee_cents)
        self.assertIsNone(data_map[job.job_id].provider_net_cents)
