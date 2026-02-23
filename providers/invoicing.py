from django.db import IntegrityError, transaction

from .models import ProviderInvoiceSequence


def next_provider_invoice_no(provider_id: int) -> str:
    """
    Retorna el siguiente numero de factura/ticket del provider.
    Concurrencia segura: usa SELECT ... FOR UPDATE sobre la secuencia.
    Ejemplo: PROV-1003-00000001
    """
    with transaction.atomic():
        seq_qs = ProviderInvoiceSequence.objects.select_for_update()
        seq = seq_qs.filter(provider_id=provider_id).first()

        if seq is None:
            try:
                ProviderInvoiceSequence.objects.create(
                    provider_id=provider_id,
                    prefix=f"PROV-{provider_id}-",
                    next_number=1,
                )
            except IntegrityError:
                # Otra transaccion pudo crearla al mismo tiempo.
                pass
            seq = seq_qs.get(provider_id=provider_id)

        prefix = seq.prefix or f"PROV-{provider_id}-"
        n = int(seq.next_number)

        seq.next_number = n + 1
        seq.save(update_fields=["next_number"])

        return f"{prefix}{n:08d}"
