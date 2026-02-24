import json

from django.test import TestCase

from clients.models import Client, ClientTicket
from jobs.fees import FEE_RULES_BY_REGION
from jobs.models import Job
from jobs.services_fee import recompute_on_demand_fee_for_open_tickets
from jobs.services_normal_client_confirm import confirm_normal_job_by_client
from providers.models import Provider, ProviderTicket
from service_type.models import ServiceType


class TestOnDemandFeeByRegion(TestCase):
    def _setup_job_with_region(self, region_code: str):
        service_type = ServiceType.objects.create(
            name=f"OnDemand Fee Region {region_code}",
            description="OnDemand Fee Region Test",
        )
        client = Client.objects.create(
            first_name="Client",
            last_name=f"Region{region_code}",
            phone_number="555-910-0001",
            email=f"client.region.{region_code.lower()}@test.local",
            country="Canada",
            province=region_code,
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name=f"Region{region_code}",
            phone_number="555-910-0002",
            email=f"provider.region.{region_code.lower()}@test.local",
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

        # Fuerza region de test y recalcula para este caso.
        pt.tax_region_code = region_code
        pt.save(update_fields=["tax_region_code"])
        ct.tax_region_code = region_code
        ct.save(update_fields=["tax_region_code"])
        recompute_on_demand_fee_for_open_tickets(pt.pk, ct.pk)

        pt.refresh_from_db()
        ct.refresh_from_db()
        return job, pt, ct

    def test_fee_rule_qc_applies(self):
        self.assertIn("QC", FEE_RULES_BY_REGION)

        _job, pt, ct = self._setup_job_with_region("QC")

        fee_pt = pt.lines.filter(line_type="fee").first()
        fee_ct = ct.lines.filter(line_type="fee").first()
        self.assertIsNotNone(fee_pt)
        self.assertIsNotNone(fee_ct)
        self.assertEqual(fee_pt.line_total_cents, fee_ct.line_total_cents)
        self.assertGreaterEqual(fee_pt.line_total_cents, 0)

    def test_fee_recomputes_after_extra(self):
        job, pt, _ct = self._setup_job_with_region("QC")
        provider_id = job.selected_provider_id

        fee_before = pt.lines.get(line_type="fee").line_total_cents

        url = f"/api/jobs/{job.job_id}/extras"
        res = self.client.post(
            url,
            data=json.dumps(
                {
                    "provider_id": provider_id,
                    "description": "Extra test",
                    "amount_cents": 1000,
                }
            ),
            content_type="application/json",
            **{"HTTP_IDEMPOTENCY_KEY": "test-key-1"},
        )
        self.assertEqual(res.status_code, 200)

        pt.refresh_from_db()
        fee_after = pt.lines.get(line_type="fee").line_total_cents
        self.assertGreaterEqual(fee_after, fee_before)

