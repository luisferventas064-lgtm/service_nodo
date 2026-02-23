from django.test import TestCase

from providers.lines import ensure_provider_base_line
from providers.models import Provider, ProviderTicket


class ProviderBaseLineTests(TestCase):
    def test_ensure_provider_base_line_idempotent(self):
        p = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="P",
            contact_last_name="One",
            phone_number="555-300-0001",
            email="provider.base.line@test.local",
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

        ensure_provider_base_line(t.pk, description="Base", unit_price_cents=1000, tax_cents=100)
        ensure_provider_base_line(t.pk, description="Base", unit_price_cents=1000, tax_cents=100)

        self.assertEqual(t.lines.count(), 1)
        t.refresh_from_db()
        self.assertEqual(t.total_cents, 1100)
