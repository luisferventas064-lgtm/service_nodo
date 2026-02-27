from django.core.exceptions import ValidationError
from django.test import TestCase

from clients.models import Client, ClientTicket, ClientTicketLine


class ClientTicketFinalizedImmutabilityTests(TestCase):
    def _make_client(self) -> Client:
        return Client.objects.create(
            first_name="Client",
            last_name="Immutable",
            phone_number="555-300-0001",
            email="client.immutable@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Immutable St",
        )

    def _make_finalized_ticket(self) -> ClientTicket:
        client = self._make_client()
        return ClientTicket.objects.create(
            client=client,
            ticket_no="CL-IMM-000001",
            ref_type="job",
            ref_id=10,
            stage=ClientTicket.Stage.FINAL,
            status=ClientTicket.Status.FINALIZED,
            subtotal_cents=8_000,
            tax_cents=1_200,
            total_cents=9_200,
            currency="CAD",
            tax_region_code="CA-QC",
        )

    def _make_open_ticket_with_line(self) -> tuple[ClientTicket, ClientTicketLine]:
        client = self._make_client()
        ticket = ClientTicket.objects.create(
            client=client,
            ticket_no="CL-IMM-000002",
            ref_type="job",
            ref_id=11,
            stage=ClientTicket.Stage.ESTIMATE,
            status=ClientTicket.Status.OPEN,
            subtotal_cents=5_000,
            tax_cents=750,
            total_cents=5_750,
            currency="CAD",
            tax_region_code="CA-QC",
        )
        line = ClientTicketLine.objects.create(
            ticket=ticket,
            line_no=1,
            line_type=ClientTicketLine.LineType.BASE,
            description="Base",
            qty=1,
            unit_price_cents=5_000,
            line_subtotal_cents=5_000,
            tax_rate_bps=1500,
            tax_cents=750,
            line_total_cents=5_750,
            tax_region_code="CA-QC",
            tax_code="GST/QST",
            meta={},
        )
        return ticket, line

    def test_finalized_ticket_cannot_modify_financial_total(self):
        ticket = self._make_finalized_ticket()
        ticket.total_cents = 9_500

        with self.assertRaisesRegex(ValidationError, "Cannot modify financial field 'total_cents'"):
            ticket.save()

    def test_finalized_ticket_cannot_change_status(self):
        ticket = self._make_finalized_ticket()
        ticket.status = ClientTicket.Status.VOID

        with self.assertRaisesRegex(ValidationError, "Cannot change status of a FINALIZED ticket"):
            ticket.save()

    def test_finalized_ticket_cannot_be_deleted(self):
        ticket = self._make_finalized_ticket()

        with self.assertRaisesRegex(ValidationError, "Cannot delete a FINALIZED ticket"):
            ticket.delete()

    def test_snapshot_hash_is_generated_when_ticket_is_finalized(self):
        ticket, _line = self._make_open_ticket_with_line()
        ticket.stage = ClientTicket.Stage.FINAL
        ticket.status = ClientTicket.Status.FINALIZED
        ticket.save()
        ticket.refresh_from_db()

        self.assertTrue(ticket.snapshot_hash)
        self.assertEqual(len(ticket.snapshot_hash), 64)
        self.assertEqual(ticket.snapshot_hash, ticket.generate_snapshot_hash())

    def test_finalized_ticket_line_create_is_blocked(self):
        ticket = self._make_finalized_ticket()

        with self.assertRaisesRegex(ValidationError, "Cannot modify lines of a FINALIZED ticket"):
            ClientTicketLine.objects.create(
                ticket=ticket,
                line_no=1,
                line_type=ClientTicketLine.LineType.BASE,
                description="Base",
                qty=1,
                unit_price_cents=1_000,
                line_subtotal_cents=1_000,
                tax_rate_bps=1500,
                tax_cents=150,
                line_total_cents=1_150,
                tax_region_code="CA-QC",
                tax_code="GST/QST",
                meta={},
            )

    def test_finalized_ticket_line_update_is_blocked(self):
        ticket, line = self._make_open_ticket_with_line()
        ticket.stage = ClientTicket.Stage.FINAL
        ticket.status = ClientTicket.Status.FINALIZED
        ticket.save()

        line.line_total_cents = 6_000
        with self.assertRaisesRegex(ValidationError, "Cannot modify lines of a FINALIZED ticket"):
            line.save()

    def test_finalized_ticket_line_delete_is_blocked(self):
        ticket, line = self._make_open_ticket_with_line()
        ticket.stage = ClientTicket.Stage.FINAL
        ticket.status = ClientTicket.Status.FINALIZED
        ticket.save()

        with self.assertRaisesRegex(ValidationError, "Cannot delete lines of a FINALIZED ticket"):
            line.delete()
