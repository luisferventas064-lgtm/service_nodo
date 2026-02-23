from __future__ import annotations

from django.db import transaction

from clients.models import ClientTicket, ClientTicketLine
from clients.totals import recalc_client_ticket_totals


@transaction.atomic
def ensure_client_fee_line(ticket_pk, *, description: str = "ON_DEMAND fee", amount_cents: int = 0):
    t = ClientTicket.objects.select_for_update().get(pk=ticket_pk)

    existing = t.lines.filter(line_type="fee").first()
    if existing:
        return existing

    next_no = (t.lines.order_by("-line_no").values_list("line_no", flat=True).first() or 0) + 1

    line = ClientTicketLine.objects.create(
        ticket=t,
        line_no=next_no,
        line_type="fee",
        description=description,
        qty=1,
        unit_price_cents=amount_cents,
        line_subtotal_cents=amount_cents,
        tax_cents=0,
        line_total_cents=amount_cents,
        tax_region_code=t.tax_region_code or "",
        tax_code="",
        meta={"model": "off", "payer": "none"},
    )

    recalc_client_ticket_totals(t.pk)
    return line
