import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from clients.models import Client, ClientTicket
from jobs.models import Job
from providers.models import Provider, ProviderTicket
from service_type.models import ServiceType


class ApiJobExtrasTests(TestCase):
    def test_add_extra_creates_provider_and_client_lines_and_is_idempotent(self):
        service_type = ServiceType.objects.create(
            name="Extras API Test",
            description="Extras API Test",
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Extras",
            phone_number="555-700-0001",
            email="client.extras.api@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Extras",
            phone_number="555-700-0002",
            email="provider.extras.api@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Provider St",
        )
        job = Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.IN_PROGRESS,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=2),
            service_type=service_type,
            client=client,
            selected_provider=provider,
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Job St",
        )

        pt = ProviderTicket.objects.create(
            provider=provider,
            ticket_no="PROV-10-000001",
            ref_type="job",
            ref_id=job.pk,
            stage="estimate",
            status="open",
            tax_region_code="CA-QC",
            subtotal_cents=0,
            tax_cents=0,
            total_cents=0,
        )
        ct = ClientTicket.objects.create(
            client=client,
            ticket_no="CL-1-000001",
            ref_type="job",
            ref_id=job.pk,
            stage="estimate",
            status="open",
            tax_region_code="CA-QC",
            subtotal_cents=0,
            tax_cents=0,
            total_cents=0,
        )

        r1 = self.client.post(
            f"/api/jobs/{job.pk}/extras",
            data=json.dumps(
                {
                    "provider_id": provider.provider_id,
                    "description": "Extra A",
                    "amount_cents": 2500,
                }
            ),
            content_type="application/json",
            **{"HTTP_IDEMPOTENCY_KEY": "k1"},
        )
        self.assertEqual(r1.status_code, 200)

        pt.refresh_from_db()
        ct.refresh_from_db()
        self.assertEqual(pt.lines.filter(line_type="extra").count(), 1)
        self.assertEqual(ct.lines.filter(line_type="extra").count(), 1)

        r2 = self.client.post(
            f"/api/jobs/{job.pk}/extras",
            data=json.dumps(
                {
                    "provider_id": provider.provider_id,
                    "description": "Extra A",
                    "amount_cents": 2500,
                }
            ),
            content_type="application/json",
            **{"HTTP_IDEMPOTENCY_KEY": "k1"},
        )
        self.assertEqual(r2.status_code, 200)

        pt.refresh_from_db()
        ct.refresh_from_db()
        self.assertEqual(pt.lines.filter(line_type="extra").count(), 1)
        self.assertEqual(ct.lines.filter(line_type="extra").count(), 1)
