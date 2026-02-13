from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from jobs.models import Job
from jobs.services_urgent_price import compute_urgent_price


class HoldConflict(Exception):
    pass


@dataclass(frozen=True)
class HoldResult:
    job_id: int
    provider_id: int
    hold_expires_at: datetime
    urgent_total: Decimal
    urgent_fee: Decimal


def hold_job_urgent(*, job_id: int, provider_id: int, hold_minutes: int = 3) -> HoldResult:
    """
    Concurrency-safe HOLD:
    - bloquea fila Job con SELECT ... FOR UPDATE
    - valida HOLD activo
    - setea hold_provider + hold_expires_at
    - congela quoted_urgent_total_price + quoted_urgent_fee_amount
    """

    now = timezone.now()
    expires = now + timezone.timedelta(minutes=hold_minutes)

    with transaction.atomic():
        job = (
            Job.objects.select_for_update()
            .select_related("hold_provider")
            .get(job_id=job_id)
        )

        # No permitir HOLD si ya esta asignado o cerrado
        if job.job_status in (
            "assigned",
            "in_progress",
            "completed",
            "confirmed",
            "cancelled",
            "expired",
        ):
            raise HoldConflict(f"Job no elegible para HOLD (status={job.job_status}).")

        # 1) Si hay HOLD activo por otro provider -> conflicto
        if job.hold_provider_id and job.hold_expires_at and job.hold_expires_at > now:
            if job.hold_provider_id != provider_id:
                raise HoldConflict(
                    f"Job en HOLD por provider_id={job.hold_provider_id} hasta {job.hold_expires_at}"
                )
            # Si es el mismo provider, extendemos/reafirmamos HOLD

        # 2) Si HOLD expiro, lo limpiamos
        if job.hold_expires_at and job.hold_expires_at <= now:
            job.hold_provider = None
            job.hold_expires_at = None

        # 3) Calcular precio urgente (usa snapshot emergency)
        urgent_total, urgent_fee = compute_urgent_price(job)

        # 4) Aplicar HOLD + congelar precio final
        job.hold_provider_id = provider_id
        job.hold_expires_at = expires
        job.quoted_urgent_total_price = urgent_total
        job.quoted_urgent_fee_amount = urgent_fee

        job.save(
            update_fields=[
                "hold_provider",
                "hold_expires_at",
                "quoted_urgent_total_price",
                "quoted_urgent_fee_amount",
            ]
        )

    return HoldResult(
        job_id=job.job_id,
        provider_id=provider_id,
        hold_expires_at=expires,
        urgent_total=urgent_total,
        urgent_fee=urgent_fee,
    )
