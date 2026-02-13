from __future__ import annotations

from providers.models import ProviderSkillPrice
from jobs.models import Job


class PriceSnapshotError(Exception):
    pass


def apply_price_snapshot_to_job(
    *,
    job: Job,
    provider_id: int,
    service_skill_id: int,
) -> None:
    psp = ProviderSkillPrice.objects.filter(
        provider_id=provider_id,
        service_skill_id=service_skill_id,
        is_active=True,
    ).first()

    if not psp:
        raise PriceSnapshotError(
            f"No active price for provider_id={provider_id} skill_id={service_skill_id}"
        )

    job.quoted_service_skill_id = service_skill_id
    job.quoted_base_price = psp.price_amount
    job.quoted_currency_code = psp.currency_code
    job.quoted_pricing_unit = psp.pricing_unit

    job.quoted_emergency_fee_type = psp.emergency_fee_type
    job.quoted_emergency_fee_value = psp.emergency_fee_value

    job.save(update_fields=[
        "quoted_service_skill_id",
        "quoted_base_price",
        "quoted_currency_code",
        "quoted_pricing_unit",
        "quoted_emergency_fee_type",
        "quoted_emergency_fee_value",
    ])
