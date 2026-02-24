from django.test import TestCase

from clients.models import Client, ClientTicket, ClientTicketLine
from jobs.ledger import upsert_platform_ledger_entry
from jobs.models import Job, PlatformLedgerEntry
from providers.models import Provider, ProviderTicket, ProviderTicketLine
from service_type.models import ServiceType


class TestLedgerFeePayer(TestCase):
    def _create_job_with_tickets(self, *, client_fee: tuple[int, int] | None, provider_fee: tuple[int, int] | None):
        service_type = ServiceType.objects.create(
            name="Ledger Fee Payer Test",
            description="Ledger Fee Payer Test",
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Ledger",
            phone_number="555-930-0001",
            email="client.ledger.fee@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Ledger",
            phone_number="555-930-0002",
            email="provider.ledger.fee@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Provider St",
        )
        job = Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.DRAFT,
            service_type=service_type,
            client=client,
            selected_provider=provider,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Job St",
        )

        pt = ProviderTicket.objects.create(
            provider=provider,
            ticket_no=f"PROV-{provider.provider_id}-000001",
            ref_type="job",
            ref_id=job.job_id,
            stage="estimate",
            status="open",
            currency="CAD",
            tax_region_code="QC",
            subtotal_cents=0,
            tax_cents=0,
            total_cents=0,
        )
        ct = ClientTicket.objects.create(
            client=client,
            ticket_no=f"CL-{client.client_id}-000001",
            ref_type="job",
            ref_id=job.job_id,
            stage="estimate",
            status="open",
            currency="CAD",
            tax_region_code="QC",
            subtotal_cents=0,
            tax_cents=0,
            total_cents=0,
        )

        ProviderTicketLine.objects.create(
            ticket=pt,
            line_no=1,
            line_type="base",
            description="Base provider",
            qty=1,
            unit_price_cents=10000,
            line_subtotal_cents=10000,
            tax_rate_bps=1300,
            tax_cents=1300,
            line_total_cents=10000,
            tax_region_code="QC",
            tax_code="",
            meta={},
        )
        ClientTicketLine.objects.create(
            ticket=ct,
            line_no=1,
            line_type="base",
            description="Base client",
            qty=1,
            unit_price_cents=10000,
            line_subtotal_cents=10000,
            tax_rate_bps=1300,
            tax_cents=1300,
            line_total_cents=10000,
            tax_region_code="QC",
            tax_code="",
            meta={},
        )

        if provider_fee is not None:
            fee_gross, fee_tax = provider_fee
            ProviderTicketLine.objects.create(
                ticket=pt,
                line_no=2,
                line_type="fee",
                description="ON_DEMAND fee | payer=provider",
                qty=1,
                unit_price_cents=fee_gross,
                line_subtotal_cents=fee_gross,
                tax_rate_bps=1300,
                tax_cents=fee_tax,
                line_total_cents=fee_gross,
                tax_region_code="QC",
                tax_code="",
                meta={},
            )

        if client_fee is not None:
            fee_gross, fee_tax = client_fee
            ClientTicketLine.objects.create(
                ticket=ct,
                line_no=2,
                line_type="fee",
                description="ON_DEMAND fee | payer=client",
                qty=1,
                unit_price_cents=fee_gross,
                line_subtotal_cents=fee_gross,
                tax_rate_bps=1300,
                tax_cents=fee_tax,
                line_total_cents=fee_gross,
                tax_region_code="QC",
                tax_code="",
                meta={},
            )

        return job

    def test_fee_paid_by_client(self):
        job = self._create_job_with_tickets(client_fee=(1130, 130), provider_fee=None)
        entry = upsert_platform_ledger_entry(job.job_id)

        self.assertEqual(entry.fee_payer, PlatformLedgerEntry.FEE_PAYER_CLIENT)
        self.assertEqual(entry.fee_cents, 1000)
        self.assertEqual(entry.platform_revenue_cents, 1000)
        self.assertEqual(entry.net_provider_cents, 8700)

    def test_fee_paid_by_provider(self):
        job = self._create_job_with_tickets(client_fee=None, provider_fee=(1130, 130))
        entry = upsert_platform_ledger_entry(job.job_id)

        self.assertEqual(entry.fee_payer, PlatformLedgerEntry.FEE_PAYER_PROVIDER)
        self.assertEqual(entry.fee_cents, 1000)
        self.assertEqual(entry.platform_revenue_cents, 1000)
        self.assertEqual(entry.net_provider_cents, 8700)

    def test_fee_split(self):
        job = self._create_job_with_tickets(client_fee=(1130, 130), provider_fee=(565, 65))
        entry = upsert_platform_ledger_entry(job.job_id)

        self.assertEqual(entry.fee_payer, PlatformLedgerEntry.FEE_PAYER_SPLIT)
        self.assertEqual(entry.fee_cents, 1500)
        self.assertEqual(entry.platform_revenue_cents, 1500)
        self.assertEqual(entry.net_provider_cents, 8700)
