from __future__ import annotations

from decimal import Decimal
from dataclasses import dataclass
from typing import Optional

from django.db import transaction

from clients.models import Client
from jobs.models import Job
from providers.models import Provider, ProviderService

from jobs.services_pricing_snapshot import apply_price_snapshot_to_job


@dataclass(frozen=True)
class CreateNormalJobInput:
    client_id: int
    service_type_id: int

    province: str
    city: str
    postal_code: str
    address_line1: str

    estimated_duration_min: int = 60

    selected_provider_id: Optional[int] = None
    selected_provider_service_id: Optional[int] = None
    selected_service_skill_id: Optional[int] = None


@transaction.atomic
def create_normal_job(data: CreateNormalJobInput) -> Job:
    job = Job.objects.create(
       job_status=Job.JobStatus.PENDING_PROVIDER_CONFIRMATION,
       
        client_id=data.client_id,
        service_type_id=data.service_type_id,
        province=data.province,
        city=data.city,
        postal_code=data.postal_code,
        address_line1=data.address_line1,
        estimated_duration_min=data.estimated_duration_min,
        selected_provider_id=data.selected_provider_id,
    )

    # Snapshot preferente desde ProviderService (nuevo menu provider -> job).
    if data.selected_provider_service_id:
        provider_service = (
            ProviderService.objects.filter(
                pk=data.selected_provider_service_id,
                is_active=True,
            )
            .only("provider_id", "price_cents", "billing_unit")
            .first()
        )
        if not provider_service:
            raise ValueError("provider_service_not_found_or_inactive")

        if data.selected_provider_id and provider_service.provider_id != data.selected_provider_id:
            raise ValueError("provider_service_provider_mismatch")

        pricing_unit = (
            "hourly"
            if provider_service.billing_unit == "hour"
            else provider_service.billing_unit
        )
        updates = [
            "provider_service",
            "quoted_base_price",
            "quoted_currency_code",
            "quoted_pricing_unit",
            "quoted_emergency_fee_type",
            "quoted_emergency_fee_value",
        ]

        if not job.selected_provider_id:
            job.selected_provider_id = provider_service.provider_id
            updates.append("selected_provider_id")

        job.provider_service = provider_service
        job.quoted_base_price = Decimal(provider_service.price_cents) / Decimal("100")
        job.quoted_currency_code = "CAD"
        job.quoted_pricing_unit = pricing_unit
        job.quoted_emergency_fee_type = "none"
        job.quoted_emergency_fee_value = Decimal("0.00")
        job.save(update_fields=updates)

    # Snapshot legado por ProviderSkillPrice.
    elif data.selected_provider_id and data.selected_service_skill_id:
        apply_price_snapshot_to_job(
            job=job,
            provider_id=data.selected_provider_id,
            service_skill_id=data.selected_service_skill_id,
        )

    return job
