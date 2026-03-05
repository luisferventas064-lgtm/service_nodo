from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.db import transaction

from clients.models import Client
from jobs.models import Job
from providers.models import ProviderService

from jobs.services_pricing_snapshot import (
    apply_price_snapshot_to_job,
    apply_provider_service_snapshot_to_job,
)


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
    client = Client.objects.only("is_phone_verified").get(pk=data.client_id)
    if not client.is_phone_verified:
        raise PermissionError("PHONE_NOT_VERIFIED")

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

        apply_provider_service_snapshot_to_job(
            job=job,
            provider_service=provider_service,
        )

    # Snapshot legado por ProviderSkillPrice.
    elif data.selected_provider_id and data.selected_service_skill_id:
        apply_price_snapshot_to_job(
            job=job,
            provider_id=data.selected_provider_id,
            service_skill_id=data.selected_service_skill_id,
        )
    else:
        raise ValueError("pricing_snapshot_source_required")

    return job
