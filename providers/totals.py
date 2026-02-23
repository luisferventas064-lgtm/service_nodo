from __future__ import annotations

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce

from providers.models import ProviderTicket


@transaction.atomic
def recalc_provider_ticket_totals(ticket_id: int) -> ProviderTicket:
    """
    Recalcula totales snapshot del ProviderTicket sumando sus ProviderTicketLine.
    - subtotal = sum(line_subtotal_cents)
    - tax      = sum(tax_cents)
    - total    = sum(line_total_cents)
    Concurrencia: lock del ticket.
    """
    t = ProviderTicket.objects.select_for_update().get(pk=ticket_id)

    agg = t.lines.aggregate(
        subtotal=Coalesce(Sum("line_subtotal_cents"), 0),
        tax=Coalesce(Sum("tax_cents"), 0),
        total=Coalesce(Sum("line_total_cents"), 0),
    )

    t.subtotal_cents = int(agg["subtotal"] or 0)
    t.tax_cents = int(agg["tax"] or 0)
    t.total_cents = int(agg["total"] or 0)

    t.save(update_fields=["subtotal_cents", "tax_cents", "total_cents"])
    return t
