from django.db import IntegrityError, transaction

from .invoicing import next_client_invoice_no
from .lines import ensure_client_base_line
from .models import ClientTicket


def ensure_client_ticket(
    *,
    client_id: int,
    ref_type: str,
    ref_id: int,
    stage: str = ClientTicket.Stage.ESTIMATE,
    status: str = ClientTicket.Status.OPEN,
    subtotal_cents: int = 0,
    tax_cents: int = 0,
    total_cents: int | None = None,
    currency: str = "CAD",
    tax_region_code: str = "",
) -> ClientTicket:
    """
    Crea el ticket si no existe (idempotente).
    Usa unique(client, ref_type, ref_id) para no duplicar.
    """
    with transaction.atomic():
        obj = ClientTicket.objects.filter(
            client_id=client_id,
            ref_type=ref_type,
            ref_id=ref_id,
        ).first()

        if obj:
            if obj.stage == ClientTicket.Stage.ESTIMATE and obj.status == ClientTicket.Status.OPEN:
                ensure_client_base_line(
                    obj.pk,
                    description="Service (estimate)",
                    unit_price_cents=obj.subtotal_cents or 0,
                    tax_cents=obj.tax_cents or 0,
                    tax_region_code=obj.tax_region_code or "",
                    tax_code="",
                )
            return obj

        ticket_no = next_client_invoice_no(client_id)
        resolved_total = subtotal_cents + tax_cents if total_cents is None else total_cents

        try:
            t = ClientTicket.objects.create(
                client_id=client_id,
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
            # Carrera contra uq_client_ticket_ref: devolver el ya creado.
            t = ClientTicket.objects.get(
                client_id=client_id,
                ref_type=ref_type,
                ref_id=ref_id,
            )

        if t.stage == ClientTicket.Stage.ESTIMATE and t.status == ClientTicket.Status.OPEN:
            ensure_client_base_line(
                t.pk,
                description="Service (estimate)",
                unit_price_cents=t.subtotal_cents or 0,
                tax_cents=t.tax_cents or 0,
                tax_region_code=t.tax_region_code or "",
                tax_code="",
            )
        return t


def finalize_client_ticket(
    *,
    client_id: int,
    ref_type: str,
    ref_id: int,
    subtotal_cents: int = 0,
    tax_cents: int = 0,
    total_cents: int | None = None,
    currency: str = "CAD",
    tax_region_code: str = "",
) -> ClientTicket:
    with transaction.atomic():
        obj = ClientTicket.objects.select_for_update().filter(
            client_id=client_id,
            ref_type=ref_type,
            ref_id=ref_id,
        ).first()
        resolved_total = subtotal_cents + tax_cents if total_cents is None else total_cents

        if obj is None:
            return ensure_client_ticket(
                client_id=client_id,
                ref_type=ref_type,
                ref_id=ref_id,
                stage=ClientTicket.Stage.FINAL,
                status=ClientTicket.Status.FINALIZED,
                subtotal_cents=subtotal_cents,
                tax_cents=tax_cents,
                total_cents=resolved_total,
                currency=currency,
                tax_region_code=tax_region_code,
            )

        obj.stage = ClientTicket.Stage.FINAL
        obj.status = ClientTicket.Status.FINALIZED
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
