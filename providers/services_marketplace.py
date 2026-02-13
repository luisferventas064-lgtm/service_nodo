from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from providers.models import Provider
from providers.models import ProviderSkillPrice


@dataclass(frozen=True)
class ProviderOffer:
    provider_id: int
    service_skill_id: int
    price_amount: Decimal
    pricing_unit: str


def list_providers_for_skill(
    *,
    service_skill_id: int,
    max_results: int = 50,
) -> List[ProviderOffer]:
    qs = (
        ProviderSkillPrice.objects.select_related("provider", "service_skill")
        .filter(service_skill_id=service_skill_id, is_active=True)
        .order_by("price_amount")[:max_results]
    )

    return [
        ProviderOffer(
            provider_id=x.provider_id,
            service_skill_id=x.service_skill_id,
            price_amount=x.price_amount,
            pricing_unit=x.pricing_unit,
        )
        for x in qs
    ]
