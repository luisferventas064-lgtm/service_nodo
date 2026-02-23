from django.test import TestCase

from providers.models import Provider, ProviderTicket, ProviderTicketLine
from providers.totals import recalc_provider_ticket_totals


class ProviderTicketTotalsTests(TestCase):
    def test_recalc_provider_ticket_totals_sums_lines(self):
        p = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="P",
            contact_last_name="One",
            phone_number="555-100-0001",
            email="provider.totals@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Provider St",
        )
        t = ProviderTicket.objects.create(
            provider=p,
            ticket_no="PROV-1-000001",
            ref_type="job",
            ref_id=1,
            stage="estimate",
            status="open",
            tax_region_code="CA-QC",
            subtotal_cents=0,
            tax_cents=0,
            total_cents=0,
        )

        ProviderTicketLine.objects.create(
            ticket=t,
            line_no=1,
            line_type="base",
            description="Base",
            qty=1,
            unit_price_cents=10000,
            line_subtotal_cents=10000,
            tax_cents=1497,
            line_total_cents=11497,
            tax_region_code="CA-QC",
            tax_code="GST/QST",
        )
        ProviderTicketLine.objects.create(
            ticket=t,
            line_no=2,
            line_type="extra",
            description="Extra",
            qty=1,
            unit_price_cents=2000,
            line_subtotal_cents=2000,
            tax_cents=300,
            line_total_cents=2300,
            tax_region_code="CA-QC",
            tax_code="GST/QST",
        )

        recalc_provider_ticket_totals(t.pk)
        t.refresh_from_db()

        self.assertEqual(t.subtotal_cents, 12000)
        self.assertEqual(t.tax_cents, 1797)
        self.assertEqual(t.total_cents, 13797)
