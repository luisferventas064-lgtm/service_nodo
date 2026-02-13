from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.db import transaction

from clients.models import Client
from jobs.models import Job
from providers.models import Provider

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

    # Snapshot si viene provider + skill
    if data.selected_provider_id and data.selected_service_skill_id:
        apply_price_snapshot_to_job(
            job=job,
            provider_id=data.selected_provider_id,
            service_skill_id=data.selected_service_skill_id,
        )

    return job
