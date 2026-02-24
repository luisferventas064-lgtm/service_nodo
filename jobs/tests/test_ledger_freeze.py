from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from clients.models import Client, ClientTicket, ClientTicketLine
from jobs.ledger import upsert_platform_ledger_entry
from jobs.models import Job, PlatformLedgerEntry
from jobs.services import confirm_service_closed_by_client, start_service_by_provider
from jobs.services_extras import add_extra_line_for_job
from jobs.services_normal_client_confirm import confirm_normal_job_by_client
from providers.models import Provider
from service_type.models import ServiceType


class TestLedgerFreeze(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Ledger Freeze Test",
            description="Ledger Freeze Test",
        )
        self.client = Client.objects.create(
            first_name="Client",
            last_name="Freeze",
            phone_number="555-950-0001",
            email="client.ledger.freeze@test.local",
            country="Canada",
            province="AB",
            city="Calgary",
            postal_code="T1X1X1",
            address_line1="1 Client St",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Freeze",
            phone_number="555-950-0002",
            email="provider.ledger.freeze@test.local",
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

    def test_upsert_does_not_change_finalized_ledger(self):
        ok, *_ = confirm_normal_job_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertTrue(ok)

        add_extra_line_for_job(
            job_id=self.job.job_id,
            provider_id=self.provider.provider_id,
            description="Extra pre-close",
            amount_cents=1000,
        )

        started = start_service_by_provider(job_id=self.job.job_id, provider_id=self.provider.provider_id)
        self.assertEqual(started, "started")

        result = confirm_service_closed_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertEqual(result, "closed_and_confirmed")

        entry = PlatformLedgerEntry.objects.get(job_id=self.job.job_id)
        self.assertTrue(entry.is_final)
        self.assertIsNotNone(entry.finalized_at)

        frozen_values = {
            "currency": entry.currency,
            "tax_region_code": entry.tax_region_code,
            "gross_cents": entry.gross_cents,
            "tax_cents": entry.tax_cents,
            "fee_cents": entry.fee_cents,
            "net_provider_cents": entry.net_provider_cents,
            "platform_revenue_cents": entry.platform_revenue_cents,
            "fee_payer": entry.fee_payer,
            "is_final": entry.is_final,
            "finalize_version": entry.finalize_version,
        }

        client_ticket = ClientTicket.objects.get(
            ref_type="job",
            ref_id=self.job.job_id,
            client_id=self.client.client_id,
        )
        next_line_no = (
            client_ticket.lines.order_by("-line_no").values_list("line_no", flat=True).first() or 0
        ) + 1
        ClientTicketLine.objects.create(
            ticket=client_ticket,
            line_no=next_line_no,
            line_type="fee",
            description="Late fee mutation after close",
            qty=1,
            unit_price_cents=2260,
            line_subtotal_cents=2260,
            tax_rate_bps=1300,
            tax_cents=260,
            line_total_cents=2260,
            tax_region_code="AB",
            tax_code="",
            meta={},
        )

        recomputed = upsert_platform_ledger_entry(self.job.job_id)

        self.assertEqual(recomputed.currency, frozen_values["currency"])
        self.assertEqual(recomputed.tax_region_code, frozen_values["tax_region_code"])
        self.assertEqual(recomputed.gross_cents, frozen_values["gross_cents"])
        self.assertEqual(recomputed.tax_cents, frozen_values["tax_cents"])
        self.assertEqual(recomputed.fee_cents, frozen_values["fee_cents"])
        self.assertEqual(recomputed.net_provider_cents, frozen_values["net_provider_cents"])
        self.assertEqual(recomputed.platform_revenue_cents, frozen_values["platform_revenue_cents"])
        self.assertEqual(recomputed.fee_payer, frozen_values["fee_payer"])
        self.assertTrue(recomputed.is_final)
        self.assertEqual(recomputed.is_final, frozen_values["is_final"])
        self.assertEqual(recomputed.finalize_version, frozen_values["finalize_version"])
