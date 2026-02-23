from django.test import TestCase

from clients.models import Client, ClientTicket, ClientTicketLine
from clients.totals import recalc_client_ticket_totals


class ClientTicketTotalsTests(TestCase):
    def test_recalc_client_ticket_totals_sums_lines(self):
        c = Client.objects.create(
            first_name="C",
            last_name="One",
            phone_number="555-200-0001",
            email="client.totals@test.local",
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

        ClientTicketLine.objects.create(
            ticket=t,
            line_no=1,
            line_type="base",
            description="Base",
            qty=1,
            unit_price_cents=5000,
            line_subtotal_cents=5000,
            tax_cents=750,
            line_total_cents=5750,
            tax_region_code="CA-QC",
            tax_code="GST/QST",
        )

        recalc_client_ticket_totals(t.pk)
        t.refresh_from_db()

        self.assertEqual(t.subtotal_cents, 5000)
        self.assertEqual(t.tax_cents, 750)
        self.assertEqual(t.total_cents, 5750)
