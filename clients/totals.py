from __future__ import annotations

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce

from clients.models import ClientTicket


@transaction.atomic
def recalc_client_ticket_totals(ticket_id: int) -> ClientTicket:
    """
    Recalcula totales snapshot del ClientTicket sumando sus ClientTicketLine.
    """
    t = ClientTicket.objects.select_for_update().get(pk=ticket_id)

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
