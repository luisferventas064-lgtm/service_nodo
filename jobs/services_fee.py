import logging

from django.db import transaction
from django.db.models import Sum

from clients.lines_fee import ensure_client_fee_line
from clients.models import ClientTicket, ClientTicketLine
from jobs.fees import compute_fee_cents, get_on_demand_fee_rule_for_region
from providers.lines_fee import ensure_provider_fee_line
from providers.models import ProviderTicket, ProviderTicketLine

logger = logging.getLogger(__name__)


def _provider_subtotal_excluding_fee(ticket_id: int) -> int:
    agg = (
        ProviderTicketLine.objects.filter(ticket_id=ticket_id)
        .exclude(line_type="fee")
        .aggregate(s=Sum("line_subtotal_cents"))
    )
    return int(agg["s"] or 0)


def _client_subtotal_excluding_fee(ticket_id: int) -> int:
    agg = (
        ClientTicketLine.objects.filter(ticket_id=ticket_id)
        .exclude(line_type="fee")
        .aggregate(s=Sum("line_subtotal_cents"))
    )
    return int(agg["s"] or 0)


def _province_from_region_code(region_code: str | None) -> str | None:
    if not region_code:
        return None
    value = str(region_code).strip().upper()
    if "-" in value:
        return value.rsplit("-", 1)[-1]
    return value


def _fee_snapshot_description(*, region_code: str | None, model: str, payer: str, value_bps: int, value_cents: int) -> str:
    region = region_code or "DEFAULT"
    if model == "percentage":
        return (
            f"ON_DEMAND fee | region={region} | model={model} | "
            f"payer={payer} | bps={value_bps}"
        )
    return (
        f"ON_DEMAND fee | region={region} | model={model} | "
        f"payer={payer} | cents={value_cents}"
    )


@transaction.atomic
def recompute_on_demand_fee_for_open_tickets(provider_ticket_id: int, client_ticket_id: int) -> int:
    """
    Recalcula fee basado en subtotal (base+extra) y tax_region_code del ticket.
    Devuelve fee_cents calculado.
    """
    pt = ProviderTicket.objects.select_for_update().get(pk=provider_ticket_id)
    ct = ClientTicket.objects.select_for_update().get(pk=client_ticket_id)

    if pt.status != "open" or ct.status != "open":
        raise PermissionError("ticket_not_open")

    raw_region = pt.tax_region_code or ct.tax_region_code
    region_for_rule = _province_from_region_code(raw_region)
    rule = get_on_demand_fee_rule_for_region(region_for_rule)

    subtotal_pt = _provider_subtotal_excluding_fee(pt.pk)
    subtotal_ct = _client_subtotal_excluding_fee(ct.pk)

    if subtotal_pt != subtotal_ct:
        logger.warning(
            "Fee subtotal mismatch for job tickets provider_ticket_id=%s client_ticket_id=%s provider_subtotal=%s client_subtotal=%s",
            pt.pk,
            ct.pk,
            subtotal_pt,
            subtotal_ct,
        )

    base = subtotal_pt
    fee_cents = compute_fee_cents(base, rule)
    snapshot_description = _fee_snapshot_description(
        region_code=region_for_rule,
        model=rule.model,
        payer=rule.payer,
        value_bps=rule.value_bps,
        value_cents=rule.value_cents,
    )

    ensure_provider_fee_line(pt.pk, amount_cents=fee_cents, description=snapshot_description)
    ensure_client_fee_line(ct.pk, amount_cents=fee_cents, description=snapshot_description)

    return fee_cents
