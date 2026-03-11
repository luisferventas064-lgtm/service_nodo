from django.db import transaction
from django.db.models import F

from providers.models import Provider, ProviderMetrics
from providers.ranking import hydrate_provider_metrics, hydrate_provider_ranking_fields


@transaction.atomic
def increment_accepted(provider_id: int) -> None:
    ProviderMetrics.objects.get_or_create(provider_id=provider_id)
    ProviderMetrics.objects.filter(provider_id=provider_id).update(
        jobs_accepted=F("jobs_accepted") + 1
    )
    _refresh_provider_metrics(provider_id)


@transaction.atomic
def increment_offers_received(provider_id: int) -> None:
    ProviderMetrics.objects.get_or_create(provider_id=provider_id)
    ProviderMetrics.objects.filter(provider_id=provider_id).update(
        offers_received_count=F("offers_received_count") + 1
    )
    _refresh_provider_metrics(provider_id)


@transaction.atomic
def record_offer_accepted(provider_id: int, *, response_seconds: float | None = None) -> None:
    metrics, _ = ProviderMetrics.objects.select_for_update().get_or_create(provider_id=provider_id)
    previous_offer_accepts = int(metrics.offers_accepted_count or 0)

    metrics.offers_accepted_count = previous_offer_accepts + 1
    metrics.jobs_accepted = int(metrics.jobs_accepted or 0) + 1

    if response_seconds is not None:
        response_minutes = max(float(response_seconds), 0.0) / 60.0
        metrics.avg_response_time = (
            ((metrics.avg_response_time or 0.0) * previous_offer_accepts) + response_minutes
        ) / float(previous_offer_accepts + 1)

    metrics.save(
        update_fields=[
            "offers_accepted_count",
            "jobs_accepted",
            "avg_response_time",
            "updated_at",
        ]
    )
    _refresh_provider_metrics(provider_id)


@transaction.atomic
def increment_completed(provider_id: int) -> None:
    Provider.objects.filter(provider_id=provider_id).update(
        completed_jobs_count=F("completed_jobs_count") + 1
    )
    ProviderMetrics.objects.get_or_create(provider_id=provider_id)
    ProviderMetrics.objects.filter(provider_id=provider_id).update(
        jobs_completed=F("jobs_completed") + 1
    )
    _refresh_provider_metrics(provider_id)


@transaction.atomic
def increment_cancelled(provider_id: int) -> None:
    Provider.objects.filter(provider_id=provider_id).update(
        cancelled_jobs_count=F("cancelled_jobs_count") + 1
    )
    ProviderMetrics.objects.get_or_create(provider_id=provider_id)
    ProviderMetrics.objects.filter(provider_id=provider_id).update(
        jobs_cancelled=F("jobs_cancelled") + 1
    )
    _refresh_provider_metrics(provider_id)


def _refresh_provider_metrics(provider_id: int, *, average_rating=None) -> None:
    provider = Provider.objects.only(
        "provider_id",
        "completed_jobs_count",
        "cancelled_jobs_count",
        "avg_rating",
        "quality_warning_active",
        "restricted_until",
        "distance_score",
    ).get(provider_id=provider_id)
    metrics, _ = ProviderMetrics.objects.get_or_create(provider_id=provider_id)

    metrics.jobs_completed = provider.completed_jobs_count or 0
    metrics.jobs_cancelled = provider.cancelled_jobs_count or 0
    metrics.jobs_accepted = max(
        metrics.jobs_accepted or 0,
        metrics.jobs_completed + metrics.jobs_cancelled,
    )

    if average_rating is not None:
        provider.avg_rating = average_rating

    hydrate_provider_metrics(provider, metrics)
    metrics.save(
        update_fields=[
            "offers_received_count",
            "offers_accepted_count",
            "jobs_completed",
            "jobs_accepted",
            "jobs_cancelled",
            "avg_response_time",
            "acceptance_rate",
            "completion_rate",
            "experience_score",
            "operational_score",
            "response_score",
            "updated_at",
        ]
    )

    hydrate_provider_ranking_fields(provider, metrics)
    update_kwargs = {
        "acceptance_rate": provider.acceptance_rate,
        "completed_jobs_count": metrics.jobs_completed,
        "cancelled_jobs_count": metrics.jobs_cancelled,
        "base_dispatch_score": provider.base_dispatch_score,
        "hybrid_score": provider.hybrid_score,
    }
    if average_rating is not None:
        update_kwargs["avg_rating"] = average_rating

    Provider.objects.filter(provider_id=provider_id).update(**update_kwargs)


@transaction.atomic
def recalc_avg_rating(provider_id: int) -> None:
    from providers.models import ProviderReview

    reviews = ProviderReview.objects.filter(provider_id=provider_id)

    total_reviews = reviews.count()
    average_rating = 0

    if total_reviews > 0:
        total_sum = sum(review.rating for review in reviews)
        average_rating = round(total_sum / total_reviews, 2)

    _refresh_provider_metrics(provider_id, average_rating=average_rating)
