from django.test import TestCase

from clients.lines import ensure_client_base_line
from clients.models import Client, ClientTicket


class ClientBaseLineTests(TestCase):
    def test_ensure_client_base_line_idempotent(self):
        c = Client.objects.create(
            first_name="C",
            last_name="One",
            phone_number="555-400-0001",
            email="client.base.line@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        t = ClientTicket.objects.create(
            client=c,
            ticket_no="CL-1-000001",
            ref_type="job",
            ref_id=1,
            stage="estimate",
            status="open",
            tax_region_code="CA-QC",
            subtotal_cents=0,
            tax_cents=0,
            total_cents=0,
        )

        ensure_client_base_line(t.pk, description="Base", unit_price_cents=500, tax_cents=50)
        ensure_client_base_line(t.pk, description="Base", unit_price_cents=500, tax_cents=50)

        self.assertEqual(t.lines.count(), 1)
        t.refresh_from_db()
        self.assertEqual(t.total_cents, 550)
