from __future__ import annotations

from django.db import transaction
from django.db.models import Sum

from providers.models import ProviderTicket


@transaction.atomic
def recalc_provider_ticket_totals(ticket_id: int) -> ProviderTicket:
    """
    Recalcula totales snapshot del ProviderTicket sumando sus ProviderTicketLine.
    - gross    = sum(line_total_cents)
    - tax      = sum(tax_cents)
    - subtotal = gross - tax
    - total    = gross
    Concurrencia: lock del ticket.
    """
    ticket = ProviderTicket.objects.select_for_update().get(pk=ticket_id)

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
