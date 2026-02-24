from __future__ import annotations

from django.db import transaction
from django.db.models import Sum

from clients.models import ClientTicket


@transaction.atomic
def recalc_client_ticket_totals(ticket_id: int) -> ClientTicket:
    """
    Recalcula totales snapshot del ClientTicket sumando sus ClientTicketLine.
    """
    ticket = ClientTicket.objects.select_for_update().get(pk=ticket_id)

    agg = ticket.lines.aggregate(
        gross=Sum("line_total_cents"),
        tax=Sum("tax_cents"),
    )

    gross = int(agg["gross"] or 0)
    tax = int(agg["tax"] or 0)
    subtotal = gross - tax
    total = gross

    ticket.subtotal_cents = subtotal
    ticket.tax_cents = tax
    ticket.total_cents = total

    ticket.save(update_fields=["subtotal_cents", "tax_cents", "total_cents"])
    return ticket
