from __future__ import annotations

from datetime import timedelta
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from django.db.models import (
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
)
from django.db.models import Func
from django.db.models.functions import Cast, Coalesce, Concat, Greatest, Least
from django.utils import timezone

from jobs.models import Job, JobDispute
from providers.models import Provider
from providers.models import ProviderService
from providers.models import ProviderSkillPrice


class Log10(Func):
    function = "LOG10"
    output_field = FloatField()


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


def _provider_has_field(name: str) -> bool:
    return any(f.name == name for f in Provider._meta.get_fields())


def marketplace_ranked_queryset(
    *,
    service_category_id: int | None = None,
    province: str | None = None,
    city: str | None = None,
    zone_id: int | None = None,
    max_price: int | None = None,
    min_rating: float | None = None,
):
    qs = ProviderService.objects.select_related("provider", "category").filter(
        is_active=True,
        category__is_active=True,
        provider__is_active=True,
        provider__is_phone_verified=True,
        provider__profile_completed=True,
        provider__billing_profile_completed=True,
        provider__accepts_terms=True,
    ).filter(
        Q(provider__restricted_until__isnull=True)
        | Q(provider__restricted_until__lt=timezone.now())
    )

    if service_category_id is not None:
        qs = qs.filter(category_id=service_category_id)

    if province:
        qs = qs.filter(provider__province=province)

    if city:
        qs = qs.filter(provider__city=city)

    if zone_id is not None:
        qs = qs.filter(provider__zone_id=zone_id)

    if max_price is not None:
        qs = qs.filter(price_cents__lte=max_price)

    if min_rating is not None and _provider_has_field("avg_rating"):
        qs = qs.filter(provider__avg_rating__gte=min_rating)

    cutoff = timezone.now() - timedelta(days=365)
    recent_disputes_subquery = (
        JobDispute.objects.filter(
            provider_id=OuterRef("provider_id"),
            status=JobDispute.DisputeStatus.RESOLVED,
            job__cancel_reason=Job.CancelReason.DISPUTE_APPROVED,
            resolved_at__gte=cutoff,
        )
        .values("provider_id")
        .annotate(total=Count("pk"))
        .values("total")[:1]
    )

    qs = qs.annotate(
        safe_rating=Coalesce(
            Cast(F("provider__avg_rating"), FloatField()),
            Value(0.0),
            output_field=FloatField(),
        ),
        safe_completed=Coalesce(F("provider__completed_jobs_count"), Value(0)),
        safe_cancelled=Coalesce(F("provider__cancelled_jobs_count"), Value(0)),
        recent_disputes_last_12m=Coalesce(
            Subquery(recent_disputes_subquery, output_field=IntegerField()),
            Value(0),
        ),
    )

    qs = qs.annotate(
        volume_score=Log10(Cast(F("safe_completed") + Value(1), FloatField())),
    )

    qs = qs.annotate(
        raw_cancellation_rate=ExpressionWrapper(
            Cast(F("safe_cancelled"), FloatField())
            / (Cast(F("safe_completed"), FloatField()) + Value(1.0)),
            output_field=FloatField(),
        )
    )

    qs = qs.annotate(
        cancellation_rate=Least(
            Greatest(F("raw_cancellation_rate"), Value(0.0)),
            Value(1.0),
        )
    )

    qs = qs.annotate(
        verified_bonus=ExpressionWrapper(
            Cast(F("provider__is_verified"), FloatField()),
            output_field=FloatField(),
        )
    )

    qs = qs.annotate(
        dispute_penalty_last_12m=ExpressionWrapper(
            Cast(F("recent_disputes_last_12m"), FloatField()) * Value(0.15),
            output_field=FloatField(),
        )
    )

    qs = qs.annotate(
        quality_component=ExpressionWrapper(
            (F("safe_rating") * Value(0.5))
            + (F("volume_score") * Value(0.3))
            - (F("cancellation_rate") * Value(0.2))
            - F("dispute_penalty_last_12m"),
            output_field=FloatField(),
        )
    )

    qs = qs.annotate(
        hybrid_score=ExpressionWrapper(
            F("quality_component") + (F("verified_bonus") * Value(0.1)),
            output_field=FloatField(),
        )
    )

    qs = qs.annotate(
        service_category_name=F("category__name"),
        zone_name=Coalesce(
            F("provider__zone__name"),
            Value(""),
        ),
        provider_display_name=Coalesce(
            F("provider__company_name"),
            Concat(
                F("provider__contact_first_name"),
                Value(" "),
                F("provider__contact_last_name"),
            ),
        ),
        provider_rating=(
            F("provider__avg_rating")
            if _provider_has_field("avg_rating")
            else Value(None, output_field=FloatField())
        ),
    )

    return qs


def search_provider_services(
    *,
    service_category_id: int,
    province: str,
    city: str | None = None,
    zone_id: int | None = None,
    max_price: int | None = None,
    min_rating: float | None = None,
    limit: int = 20,
    offset: int = 0,
):
    """
    Discovery search for provider-defined service menu.
    Returns dict rows already shaped for UI consumption.
    """
    qs = marketplace_ranked_queryset(
        service_category_id=service_category_id,
        province=province,
        city=city,
        zone_id=zone_id,
        max_price=max_price,
        min_rating=min_rating,
    ).order_by("-hybrid_score", "-safe_rating", "price_cents", "provider_id")

    limit = max(1, min(limit or 20, 100))
    offset = max(0, offset or 0)

    qs = qs[offset : offset + limit]

    return qs.values(
        "id",
        "price_cents",
        "service_category_name",
        "provider_id",
        "provider_display_name",
        "provider_rating",
        "safe_rating",
        "safe_completed",
        "safe_cancelled",
        "volume_score",
        "verified_bonus",
        "cancellation_rate",
        "hybrid_score",
    )
