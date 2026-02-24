from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from clients.models import Client, ClientTicket, ClientTicketLine
from jobs.ledger import rebuild_platform_ledger_for_job, upsert_platform_ledger_entry
from jobs.models import Job, PlatformLedgerEntry
from jobs.services import confirm_service_closed_by_client, start_service_by_provider
from jobs.services_extras import add_extra_line_for_job
from jobs.services_normal_client_confirm import confirm_normal_job_by_client
from providers.models import Provider
from service_type.models import ServiceType


class TestLedgerRebuild(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Ledger Rebuild Test",
            description="Ledger Rebuild Test",
        )
        self.client = Client.objects.create(
            first_name="Client",
            last_name="Rebuild",
            phone_number="555-960-0001",
            email="client.ledger.rebuild@test.local",
            country="Canada",
            province="AB",
            city="Calgary",
            postal_code="T1X1X1",
            address_line1="1 Client St",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Rebuild",
            phone_number="555-960-0002",
            email="provider.ledger.rebuild@test.local",
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

    def test_rebuild_updates_final_ledger_with_audit_trail(self):
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

        frozen_entry = PlatformLedgerEntry.objects.get(job_id=self.job.job_id)
        self.assertTrue(frozen_entry.is_final)

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

        unchanged = upsert_platform_ledger_entry(self.job.job_id)
        self.assertEqual(unchanged.fee_cents, frozen_entry.fee_cents)
        self.assertTrue(unchanged.is_final)

        rebuilt = rebuild_platform_ledger_for_job(
            self.job.job_id,
            run_id="TEST",
            reason="fix",
        )
        self.assertEqual(rebuilt.rebuild_count, 1)
        self.assertIsNotNone(rebuilt.last_rebuild_at)
        self.assertEqual(rebuilt.last_rebuild_run_id, "TEST")
        self.assertEqual(rebuilt.last_rebuild_reason, "fix")
        self.assertTrue(rebuilt.is_final)
        self.assertGreater(rebuilt.fee_cents, frozen_entry.fee_cents)

    def test_rebuild_creates_missing_ledger_entry_for_backfill(self):
        self.assertFalse(PlatformLedgerEntry.objects.filter(job_id=self.job.job_id).exists())

        rebuilt = rebuild_platform_ledger_for_job(
            self.job.job_id,
            run_id="BACKFILL_TEST",
            reason="backfill_missing_ledger",
        )

        self.assertTrue(PlatformLedgerEntry.objects.filter(job_id=self.job.job_id).exists())
        self.assertFalse(rebuilt.is_final)
        self.assertGreaterEqual(rebuilt.rebuild_count, 1)
        self.assertEqual(rebuilt.last_rebuild_run_id, "BACKFILL_TEST")
        self.assertEqual(rebuilt.last_rebuild_reason, "backfill_missing_ledger")
