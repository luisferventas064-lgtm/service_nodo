from __future__ import annotations

from django.db import transaction

from jobs.taxes_apply import apply_tax_snapshot_to_line
from providers.models import ProviderTicket, ProviderTicketLine
from providers.totals import recalc_provider_ticket_totals


@transaction.atomic
def ensure_provider_fee_line(ticket_pk, amount_cents: int = 0, description: str | None = None):
    t = ProviderTicket.objects.select_for_update().get(pk=ticket_pk)

    existing = t.lines.filter(line_type="fee").first()
    if existing:
        original_values = {
            "description": existing.description,
            "unit_price_cents": existing.unit_price_cents,
            "line_subtotal_cents": existing.line_subtotal_cents,
            "line_total_cents": existing.line_total_cents,
            "tax_region_code": existing.tax_region_code,
            "tax_rate_bps": existing.tax_rate_bps,
            "tax_cents": existing.tax_cents,
        }

        next_description = existing.description if description is None else description
        existing.description = next_description
        existing.unit_price_cents = amount_cents
        existing.line_subtotal_cents = amount_cents
        existing.line_total_cents = amount_cents
        apply_tax_snapshot_to_line(existing, region_code=t.tax_region_code)

        changed_fields = [
            field_name
            for field_name, old_value in original_values.items()
            if getattr(existing, field_name) != old_value
        ]
        if changed_fields:
            existing.save(update_fields=changed_fields)
            recalc_provider_ticket_totals(t.pk)
        return existing

    next_no = (t.lines.order_by("-line_no").values_list("line_no", flat=True).first() or 0) + 1

    line = ProviderTicketLine(
        ticket=t,
        line_no=next_no,
        line_type="fee",
        description=description or "ON_DEMAND fee",
        qty=1,
        unit_price_cents=amount_cents,
        line_subtotal_cents=amount_cents,
        line_total_cents=amount_cents,
        tax_region_code=t.tax_region_code or None,
        tax_code="",
        meta={"model": "off", "payer": "none"},
    )
    apply_tax_snapshot_to_line(line, region_code=t.tax_region_code)
    line.save()

    recalc_provider_ticket_totals(t.pk)
    return line
