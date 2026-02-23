from django.db import IntegrityError, transaction

from .models import ClientInvoiceSequence


def next_client_invoice_no(client_id: int) -> str:
    """
    Retorna el siguiente numero de factura/ticket del client.
    Concurrencia segura: usa SELECT ... FOR UPDATE sobre la secuencia.
    Ejemplo: CLNT-501-00000001
    """
    with transaction.atomic():
        seq_qs = ClientInvoiceSequence.objects.select_for_update()
        seq = seq_qs.filter(client_id=client_id).first()

        if seq is None:
            try:
                ClientInvoiceSequence.objects.create(
                    client_id=client_id,
                    prefix=f"CLNT-{client_id}-",
                    next_number=1,
                )
            except IntegrityError:
                # Otra transaccion pudo crearla al mismo tiempo.
                pass
            seq = seq_qs.get(client_id=client_id)

        prefix = seq.prefix or f"CLNT-{client_id}-"
        n = int(seq.next_number)

        seq.next_number = n + 1
        seq.save(update_fields=["next_number"])

        return f"{prefix}{n:08d}"
