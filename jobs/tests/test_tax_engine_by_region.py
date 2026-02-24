from django.test import TestCase

from clients.models import Client, ClientTicket
from jobs.models import Job
from jobs.taxes_apply import apply_tax_snapshot_to_line
from jobs.services_fee import recompute_on_demand_fee_for_open_tickets
from jobs.services_normal_client_confirm import confirm_normal_job_by_client
from jobs.taxes import TAX_RULES_BY_REGION
from providers.models import Provider, ProviderTicket
from clients.totals import recalc_client_ticket_totals
from providers.totals import recalc_provider_ticket_totals
from service_type.models import ServiceType


class TestTaxEngineByRegion(TestCase):
    def _setup_job_with_region(self, region_code: str):
        service_type = ServiceType.objects.create(
            name=f"Tax Engine Region {region_code}",
            description="Tax Engine Region Test",
        )
        client = Client.objects.create(
            first_name="Client",
            last_name=f"Tax{region_code}",
            phone_number="555-920-0001",
            email=f"client.tax.{region_code.lower()}@test.local",
            country="Canada",
            province=region_code,
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name=f"Tax{region_code}",
            phone_number="555-920-0002",
            email=f"provider.tax.{region_code.lower()}@test.local",
            province=region_code,
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Provider St",
        )
        job = Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            service_type=service_type,
            client=client,
            selected_provider=provider,
            country="Canada",
            province=region_code,
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Job St",
        )

        ok, *_ = confirm_normal_job_by_client(job_id=job.job_id, client_id=client.client_id)
        self.assertTrue(ok)

        pt = ProviderTicket.objects.get(provider=provider, ref_type="job", ref_id=job.job_id)
        ct = ClientTicket.objects.get(client=client, ref_type="job", ref_id=job.job_id)

        pt.tax_region_code = region_code
        pt.save(update_fields=["tax_region_code"])
        ct.tax_region_code = region_code
        ct.save(update_fields=["tax_region_code"])
        recompute_on_demand_fee_for_open_tickets(pt.pk, ct.pk)
        for line in pt.lines.all():
            apply_tax_snapshot_to_line(line, region_code=region_code)
            line.save(update_fields=["tax_region_code", "tax_rate_bps", "tax_cents"])
        for line in ct.lines.all():
            apply_tax_snapshot_to_line(line, region_code=region_code)
            line.save(update_fields=["tax_region_code", "tax_rate_bps", "tax_cents"])
        recalc_provider_ticket_totals(pt.pk)
        recalc_client_ticket_totals(ct.pk)

        pt.refresh_from_db()
        ct.refresh_from_db()
        return job, pt, ct

    def test_qc_tax_snapshot_is_applied_on_lines(self):
        self.assertIn("QC", TAX_RULES_BY_REGION)

        _job, pt, ct = self._setup_job_with_region("QC")

        plines = list(pt.lines.all())
        self.assertTrue(plines)

        for ln in plines:
            self.assertEqual((ln.tax_region_code or "").upper(), "QC")
            self.assertGreaterEqual(ln.tax_rate_bps, 0)
            self.assertGreaterEqual(ln.tax_cents, 0)

        clines = list(ct.lines.all())
        self.assertEqual(len(clines), len(plines))

        for ln in clines:
            self.assertEqual((ln.tax_region_code or "").upper(), "QC")
            self.assertGreaterEqual(ln.tax_rate_bps, 0)
            self.assertGreaterEqual(ln.tax_cents, 0)

    def test_tax_cents_sum_matches_ticket_tax(self):
        _job, pt, ct = self._setup_job_with_region("QC")

        pt.refresh_from_db()
        ct.refresh_from_db()

        sum_tax_pt = sum(int(x.tax_cents or 0) for x in pt.lines.all())
        sum_tax_ct = sum(int(x.tax_cents or 0) for x in ct.lines.all())

        self.assertEqual(pt.tax_cents, sum_tax_pt)
        self.assertEqual(ct.tax_cents, sum_tax_ct)
