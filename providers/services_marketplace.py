from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List

from django.db.models import (
    BooleanField,
    Case,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    Func,
    IntegerField,
    OuterRef,
    Q,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce, Concat, Greatest, Least

from providers.models import ProviderService
from providers.models import ProviderServiceArea
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


def marketplace_ranked_queryset(
    *,
    province: str | None = None,
    city: str | None = None,
    service_type_id: int | None = None,
):
    qs = (
        ProviderService.objects.select_related("provider", "service_type")
        .filter(
            is_active=True,
            provider__is_active=True,
        )
    )

    area_filters = {
        "provider_id": OuterRef("provider_id"),
        "is_active": True,
    }
    if province:
        area_filters["province"] = province
    if city:
        area_filters["city"] = city
    active_area_subquery = ProviderServiceArea.objects.filter(**area_filters)
    qs = qs.annotate(has_active_area=Exists(active_area_subquery)).filter(has_active_area=True)

    if service_type_id:
        qs = qs.filter(service_type_id=service_type_id)

    qs = qs.annotate(
        safe_rating=Coalesce(
            Cast(F("provider__avg_rating"), FloatField()),
            Value(0.0),
            output_field=FloatField(),
        ),
        safe_completed=Coalesce(F("provider__completed_jobs_count"), Value(0)),
        safe_cancelled=Coalesce(F("provider__cancelled_jobs_count"), Value(0)),
        verified_bonus=Cast(F("provider__is_verified"), FloatField()),
        service_type_name=F("service_type__name"),
        provider_display_name=Coalesce(
            F("provider__company_name"),
            Concat(
                F("provider__contact_first_name"),
                Value(" "),
                F("provider__contact_last_name"),
            ),
        ),
        zone_name=Coalesce(F("provider__zone__name"), Value("")),
    ).annotate(
        cancellation_rate=ExpressionWrapper(
            Cast(F("safe_cancelled"), FloatField())
            / (Cast(F("safe_completed"), FloatField()) + Value(1.0)),
            output_field=FloatField(),
        ),
    ).annotate(
        cancellation_rate=Least(
            Greatest(F("cancellation_rate"), Value(0.0)),
            Value(1.0),
        ),
        hybrid_score=ExpressionWrapper(
            F("safe_rating") + (F("verified_bonus") * Value(0.1)),
            output_field=FloatField(),
        ),
    )

    from providers.models import ProviderCertificate, ProviderInsurance
    from service_type.models import RequiredCertification

    # Insurance verified
    insurance_subquery = ProviderInsurance.objects.filter(
        provider=OuterRef("provider_id"),
        has_insurance=True,
        is_verified=True,
    )

    # Certificate verified (cualquier certificado valido)
    certificate_subquery = ProviderCertificate.objects.filter(
        provider=OuterRef("provider_id"),
        status="verified",
    )

    qs = qs.annotate(
        has_verified_insurance=Exists(insurance_subquery),
        has_verified_certificate=Exists(certificate_subquery),
    )

    qs = qs.annotate(
        compliance_score=Case(
            When(
                Q(has_verified_certificate=True) & Q(has_verified_insurance=True),
                then=Value(3),
            ),
            When(
                Q(has_verified_certificate=True),
                then=Value(2),
            ),
            When(
                Q(has_verified_insurance=True),
                then=Value(1),
            ),
            default=Value(0),
            output_field=IntegerField(),
        )
    )

    return qs.order_by(
        "-compliance_score",
        "-safe_rating",
        "-safe_completed",
        "provider_id",
    )


def search_provider_services(
    *,
    service_type_id: int,
    province: str,
    city: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    qs = marketplace_ranked_queryset(
        service_type_id=service_type_id,
        province=province,
        city=city,
    )

    limit = max(1, min(limit or 20, 100))
    offset = max(0, offset or 0)
    qs = qs[offset : offset + limit]

    return qs.values(
        "id",
        "price_cents",
        "service_type_id",
        "service_type_name",
        "provider_id",
        "provider_display_name",
        "safe_rating",
        "safe_completed",
        "safe_cancelled",
        "verified_bonus",
        "cancellation_rate",
        "hybrid_score",
        "zone_name",
    )
