from __future__ import annotations

from decimal import Decimal
from typing import Optional

from providers.models import ProviderSkillPrice


class PriceNotFound(Exception):
    pass


def get_skill_price(
    *,
    provider_id: int,
    service_skill_id: int,
    active_only: bool = True,
) -> Optional[ProviderSkillPrice]:
    qs = ProviderSkillPrice.objects.select_related("provider", "service_skill").filter(
        provider_id=provider_id,
        service_skill_id=service_skill_id,
    )
    if active_only:
        qs = qs.filter(is_active=True)

    return qs.first()


def get_skill_price_amount(
    *,
    provider_id: int,
    service_skill_id: int,
) -> Decimal:
    p = get_skill_price(provider_id=provider_id, service_skill_id=service_skill_id, active_only=True)
    if not p:
        raise PriceNotFound(f"No active price for provider_id={provider_id} skill_id={service_skill_id}")
    return p.price_amount
