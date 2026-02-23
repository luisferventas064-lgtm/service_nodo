from __future__ import annotations

from django.db import transaction

from providers.models import ProviderTicket, ProviderTicketLine
from providers.totals import recalc_provider_ticket_totals


@transaction.atomic
def ensure_provider_base_line(
    ticket_id: int,
    *,
    description: str,
    unit_price_cents: int,
    tax_cents: int = 0,
    tax_region_code: str = "",
    tax_code: str = "",
) -> ProviderTicketLine:
    """
    Garantiza que exista la linea BASE (line_no=1) para el ticket.
    Idempotente: si ya existe, no la duplica.
    """
    t = ProviderTicket.objects.select_for_update().get(pk=ticket_id)

    line, _created = ProviderTicketLine.objects.get_or_create(
        ticket=t,
        line_no=1,
        defaults=dict(
            line_type="base",
            description=description,
            qty=1,
            unit_price_cents=unit_price_cents,
            line_subtotal_cents=unit_price_cents,
            tax_cents=tax_cents,
            line_total_cents=unit_price_cents + tax_cents,
            tax_region_code=tax_region_code or t.tax_region_code or "",
            tax_code=tax_code,
            meta={},
        ),
    )

    # Si existia pero no era base (caso raro), no lo tocamos aqui.
    recalc_provider_ticket_totals(t.pk)
    return line
