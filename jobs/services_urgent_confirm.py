from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from jobs.models import Job


class ConfirmConflict(Exception):
    pass


@dataclass(frozen=True)
class ConfirmResult:
    job_id: int
    provider_id: int
    job_status: str
    urgent_total: Decimal
    urgent_fee: Decimal


from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from jobs.models import Job


class ConfirmConflict(Exception):
    pass


@dataclass(frozen=True)
class ConfirmResult:
    job_id: int
    provider_id: int
    job_status: str
    urgent_total: Decimal
    urgent_fee: Decimal


def confirm_urgent_job(*, job_id: int, provider_id: int) -> ConfirmResult:
    """
    Concurrency-safe confirm (DOBLE CONFIRMACIÓN):
    - bloquea fila Job
    - valida HOLD activo y que pertenezca al provider
    - valida snapshot urgent price presente
    - fija selected_provider_id (provider ganador)
    - cambia status a pending_client_confirmation
    - limpia HOLD
    """

    now = timezone.now()

    with transaction.atomic():
        job = Job.objects.select_for_update().get(job_id=job_id)

        # HOLD debe existir
        if not job.hold_provider_id or not job.hold_expires_at:
            raise ConfirmConflict("No hay HOLD activo para este job.")

        # HOLD no debe estar expirado
        if job.hold_expires_at <= now:
            raise ConfirmConflict(f"HOLD expirado en {job.hold_expires_at}.")

        # Debe ser el mismo provider
        if job.hold_provider_id != provider_id:
            raise ConfirmConflict(
                f"Job en HOLD por provider_id={job.hold_provider_id}, no por provider_id={provider_id}."
            )

        # Debe existir precio urgente congelado
        if job.quoted_urgent_total_price is None or job.quoted_urgent_fee_amount is None:
            raise ConfirmConflict("Precio urgente no está congelado en el job.")

        # Estados válidos para confirmar (ajusta si lo necesitas)
        if job.job_status not in ("posted", "hold", "pending_provider_confirmation", "pending_client_confirmation"):
            raise ConfirmConflict(f"Estado inválido para confirmar urgencia: {job.job_status}")

        # ✅ Idempotencia segura
        if job.job_status == "pending_client_confirmation":
            if job.selected_provider_id and job.selected_provider_id != provider_id:
                raise ConfirmConflict(
                    f"Ya existe selected_provider_id={job.selected_provider_id}, no coincide con provider_id={provider_id}."
                )
            return ConfirmResult(
                job_id=job.job_id,
                provider_id=provider_id,
                job_status=job.job_status,
                urgent_total=job.quoted_urgent_total_price,
                urgent_fee=job.quoted_urgent_fee_amount,
            )

        # ✅ Fijar provider ganador (reserva real)
        job.selected_provider_id = provider_id

        # Provider confirmó -> ahora espera confirmación del cliente
        job.job_status = "pending_client_confirmation"

        # Limpieza HOLD
        job.hold_provider = None
        job.hold_expires_at = None

        job.save(update_fields=[
            "selected_provider",
            "job_status",
            "hold_provider",
            "hold_expires_at",
        ])

        return ConfirmResult(
            job_id=job.job_id,
            provider_id=provider_id,
            job_status=job.job_status,
            urgent_total=job.quoted_urgent_total_price,
            urgent_fee=job.quoted_urgent_fee_amount,
        )