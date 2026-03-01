from django.db import IntegrityError, transaction

from .invoicing import next_provider_invoice_no
from .lines import ensure_provider_base_line
from .models import ProviderTicket


def ensure_provider_ticket(
    *,
    provider_id: int,
    ref_type: str,
    ref_id: int,
    stage: str = ProviderTicket.Stage.ESTIMATE,
    status: str = ProviderTicket.Status.OPEN,
    subtotal_cents: int = 0,
    tax_cents: int = 0,
    total_cents: int | None = None,
    currency: str = "CAD",
    tax_region_code: str = "",
) -> ProviderTicket:
    """
    Create the ticket if it does not exist (idempotent).
    Usa unique(provider, ref_type, ref_id) para no duplicar.
    """
    with transaction.atomic():
        obj = ProviderTicket.objects.filter(
            provider_id=provider_id,
            ref_type=ref_type,
            ref_id=ref_id,
        ).first()

        if obj:
            if obj.stage == ProviderTicket.Stage.ESTIMATE and obj.status == ProviderTicket.Status.OPEN:
                ensure_provider_base_line(
                    obj.pk,
                    description="Service (estimate)",
                    unit_price_cents=obj.subtotal_cents or 0,
                    tax_cents=obj.tax_cents or 0,
                    tax_region_code=obj.tax_region_code or "",
                    tax_code="",
                )
            return obj

        ticket_no = next_provider_invoice_no(provider_id)
        resolved_total = subtotal_cents + tax_cents if total_cents is None else total_cents

        try:
            t = ProviderTicket.objects.create(
                provider_id=provider_id,
                ref_type=ref_type,
                ref_id=ref_id,
                ticket_no=ticket_no,
                stage=stage,
                status=status,
                subtotal_cents=subtotal_cents,
                tax_cents=tax_cents,
                total_cents=resolved_total,
                currency=currency,
                tax_region_code=tax_region_code,
            )
        except IntegrityError:
            # Carrera contra uq_provider_ticket_ref: devolver el ya creado.
            t = ProviderTicket.objects.get(
                provider_id=provider_id,
                ref_type=ref_type,
                ref_id=ref_id,
            )

        if t.stage == ProviderTicket.Stage.ESTIMATE and t.status == ProviderTicket.Status.OPEN:
            ensure_provider_base_line(
                t.pk,
                description="Service (estimate)",
                unit_price_cents=t.subtotal_cents or 0,
                tax_cents=t.tax_cents or 0,
                tax_region_code=t.tax_region_code or "",
                tax_code="",
            )
        return t


def finalize_provider_ticket(
    *,
    provider_id: int,
    ref_type: str,
    ref_id: int,
    subtotal_cents: int = 0,
    tax_cents: int = 0,
    total_cents: int | None = None,
    currency: str = "CAD",
    tax_region_code: str = "",
) -> ProviderTicket:
    with transaction.atomic():
        obj = ProviderTicket.objects.select_for_update().filter(
            provider_id=provider_id,
            ref_type=ref_type,
            ref_id=ref_id,
        ).first()
        resolved_total = subtotal_cents + tax_cents if total_cents is None else total_cents

        if obj is None:
            return ensure_provider_ticket(
                provider_id=provider_id,
                ref_type=ref_type,
                ref_id=ref_id,
                stage=ProviderTicket.Stage.FINAL,
                status=ProviderTicket.Status.FINALIZED,
                subtotal_cents=subtotal_cents,
                tax_cents=tax_cents,
                total_cents=resolved_total,
                currency=currency,
                tax_region_code=tax_region_code,
            )

        obj.stage = ProviderTicket.Stage.FINAL
        obj.status = ProviderTicket.Status.FINALIZED
        obj.subtotal_cents = subtotal_cents
        obj.tax_cents = tax_cents
        obj.total_cents = resolved_total
        obj.currency = currency
        obj.tax_region_code = tax_region_code
        obj.save(
            update_fields=[
                "stage",
                "status",
                "subtotal_cents",
                "tax_cents",
                "total_cents",
                "currency",
                "tax_region_code",
            ]
        )
        return obj
