from django.db import transaction
from django.db.models import F

from providers.models import Provider


@transaction.atomic
def increment_completed(provider_id: int) -> None:
    Provider.objects.filter(provider_id=provider_id).update(
        completed_jobs_count=F("completed_jobs_count") + 1
    )
    _recalc_acceptance_rate(provider_id)


@transaction.atomic
def increment_cancelled(provider_id: int) -> None:
    Provider.objects.filter(provider_id=provider_id).update(
        cancelled_jobs_count=F("cancelled_jobs_count") + 1
    )
    _recalc_acceptance_rate(provider_id)


def _recalc_acceptance_rate(provider_id: int) -> None:
    provider = Provider.objects.only(
        "completed_jobs_count",
        "cancelled_jobs_count",
    ).get(provider_id=provider_id)

    total = (provider.completed_jobs_count or 0) + (provider.cancelled_jobs_count or 0)

    if total == 0:
        rate = 0
    else:
        rate = (provider.completed_jobs_count / total) * 100

    Provider.objects.filter(provider_id=provider_id).update(
        acceptance_rate=round(rate, 2)
    )


@transaction.atomic
def recalc_avg_rating(provider_id: int) -> None:
    from providers.models import ProviderReview

    reviews = ProviderReview.objects.filter(provider_id=provider_id)

    total_reviews = reviews.count()

    if total_reviews == 0:
        Provider.objects.filter(provider_id=provider_id).update(
            avg_rating=0
        )
        return

    total_sum = sum(review.rating for review in reviews)

    avg = total_sum / total_reviews

    Provider.objects.filter(provider_id=provider_id).update(
        avg_rating=round(avg, 2)
    )
