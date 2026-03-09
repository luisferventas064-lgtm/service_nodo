from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from providers.models import ProviderService, ProviderSkillPrice
from jobs.models import Job


class PriceSnapshotError(Exception):
    pass


MONEY_Q = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def _cents_to_money(value: int) -> Decimal:
    return _money(Decimal(value) / Decimal("100"))


def job_snapshot_subtotal_cents(job: Job) -> int:
    job.require_pricing_snapshot()
    subtotal_cents = job.snapshot_base_price_cents()
    if subtotal_cents is None:
        raise PriceSnapshotError("Job pricing snapshot is missing base price.")
    return int(subtotal_cents)


def job_snapshot_total_cents(job: Job) -> int:
    job.require_pricing_snapshot()
    total_cents = job.snapshot_total_price_cents()
    if total_cents is None:
        raise PriceSnapshotError("Job pricing snapshot is missing total price.")
    return int(total_cents)


def job_snapshot_currency(job: Job) -> str:
    job.require_pricing_snapshot()
    currency = job.snapshot_currency_code()
    if not currency:
        raise PriceSnapshotError("Job pricing snapshot is missing currency.")
    return currency


def apply_provider_service_snapshot_to_job(
    *,
    job: Job,
    provider_service: ProviderService,
) -> None:
    pricing_unit = (
        "hourly"
        if provider_service.billing_unit == "hour"
        else provider_service.billing_unit
    )
    base_cents = int(provider_service.price_cents)

    update_fields = [
        "provider_service",
        "provider_service_name_snapshot",
        "quoted_service_skill_id",
        "quoted_base_price",
        "quoted_base_price_cents",
        "quoted_currency_code",
        "quoted_currency",
        "quoted_pricing_unit",
        "quoted_emergency_fee_type",
        "quoted_emergency_fee_value",
        "quoted_pricing_source",
        "quoted_provider_service_id",
        "quoted_total_price_cents",
    ]

    if not job.selected_provider_id:
        job.selected_provider_id = provider_service.provider_id
        update_fields.append("selected_provider_id")

    job.provider_service = provider_service
    job.provider_service_name_snapshot = (provider_service.custom_name or "").strip()
    job.quoted_service_skill_id = None
    job.quoted_base_price = _cents_to_money(base_cents)
    job.quoted_base_price_cents = base_cents
    job.quoted_currency_code = "CAD"
    job.quoted_currency = "CAD"
    job.quoted_pricing_unit = pricing_unit
    job.quoted_emergency_fee_type = "none"
    job.quoted_emergency_fee_value = Decimal("0.00")
    job.quoted_pricing_source = "ProviderService"
    job.quoted_provider_service_id = provider_service.pk
    job.quoted_total_price_cents = base_cents
    job.save(update_fields=update_fields)


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

    base_price = _money(psp.price_amount)

    job.quoted_service_skill_id = service_skill_id
    job.quoted_base_price = base_price
    job.quoted_base_price_cents = Job._decimal_to_cents(base_price)
    job.quoted_currency_code = psp.currency_code
    job.quoted_currency = (psp.currency_code or "").strip().upper()
    job.quoted_pricing_unit = psp.pricing_unit

    job.quoted_emergency_fee_type = psp.emergency_fee_type
    job.quoted_emergency_fee_value = psp.emergency_fee_value
    job.quoted_pricing_source = "ProviderSkillPrice"
    job.quoted_provider_service_id = None
    job.quoted_total_price_cents = job.quoted_base_price_cents

    job.save(update_fields=[
        "quoted_service_skill_id",
        "quoted_base_price",
        "quoted_base_price_cents",
        "quoted_currency_code",
        "quoted_currency",
        "quoted_pricing_unit",
        "quoted_emergency_fee_type",
        "quoted_emergency_fee_value",
        "quoted_pricing_source",
        "quoted_provider_service_id",
        "quoted_total_price_cents",
    ])
