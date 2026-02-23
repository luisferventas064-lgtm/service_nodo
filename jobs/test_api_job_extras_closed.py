import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from clients.models import Client, ClientTicket
from jobs.models import Job
from providers.models import Provider, ProviderTicket
from service_type.models import ServiceType


class ApiJobExtrasClosedTests(TestCase):
    def test_add_extra_forbidden_after_ticket_finalized(self):
        service_type = ServiceType.objects.create(
            name="Extras Closed API Test",
            description="Extras Closed API Test",
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Closed",
            phone_number="555-800-0001",
            email="client.closed.api@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Closed",
            phone_number="555-800-0002",
            email="provider.closed.api@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Provider St",
        )
        job = Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.CONFIRMED,
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

        ProviderTicket.objects.create(
            provider=provider,
            ticket_no="PROV-10-000001",
            ref_type="job",
            ref_id=job.pk,
            stage="final",
            status="finalized",
            tax_region_code="CA-QC",
            subtotal_cents=0,
            tax_cents=0,
            total_cents=0,
        )
        ClientTicket.objects.create(
            client=client,
            ticket_no="CL-1-000001",
            ref_type="job",
            ref_id=job.pk,
            stage="final",
            status="finalized",
            tax_region_code="CA-QC",
            subtotal_cents=0,
            tax_cents=0,
            total_cents=0,
        )

        r = self.client.post(
            f"/api/jobs/{job.pk}/extras",
            data=json.dumps(
                {
                    "provider_id": provider.provider_id,
                    "description": "Extra X",
                    "amount_cents": 1000,
                }
            ),
            content_type="application/json",
            **{"HTTP_IDEMPOTENCY_KEY": "k-closed"},
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {"error": "ticket_not_open"})
