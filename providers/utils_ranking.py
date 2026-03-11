from django.utils import timezone


def _normalize_rate(value):
    if value is None:
        return 0.0
    rate = float(value)
    if rate > 1.0:
        rate = rate / 100.0
    return max(0.0, min(rate, 1.0))


def distance_score(distance_km):
    return max(0.0, 1 - (float(distance_km) / 50))


def response_score(response_minutes):
    if response_minutes is None:
        return 0.5
    return max(0.0, 1 - (float(response_minutes) / 60))


def provider_base_dispatch_score(
    *,
    rating=0,
    response_minutes=None,
    acceptance_rate=None,
    completion_rate=None,
):
    rating_score = float(rating) / 5 if rating else 0
    acceptance_score = _normalize_rate(acceptance_rate)
    completion_score = _normalize_rate(completion_rate)

    return (
        rating_score * 0.2
        + response_score(response_minutes) * 0.15
        + acceptance_score * 0.1
        + completion_score * 0.1
    )


def fairness_score(last_job_assigned_at, now=None):
    if not last_job_assigned_at:
        return 1.0

    current_time = now or timezone.now()
    seconds_since_last_assignment = max(
        (current_time - last_job_assigned_at).total_seconds(),
        0,
    )
    hours_since_last_assignment = seconds_since_last_assignment / 3600
    return min(hours_since_last_assignment / 4, 1.0)


def provider_runtime_dispatch_score(distance_km, *, last_job_assigned_at=None, now=None):
    provider_fairness_score = fairness_score(last_job_assigned_at, now=now)
    return (
        distance_score(distance_km) * 0.35
        + provider_fairness_score * 0.1
    )


def dispatch_score_from_base(
    *,
    base_dispatch_score,
    distance_km,
    last_job_assigned_at=None,
    random_bonus=0.0,
    now=None,
):
    return (
        float(base_dispatch_score or 0.0)
        + provider_runtime_dispatch_score(
            distance_km,
            last_job_assigned_at=last_job_assigned_at,
            now=now,
        )
        + float(random_bonus or 0.0)
    )


def provider_ranking_score(
    distance_km,
    rating=0,
    response_minutes=None,
    acceptance_rate=None,
    completion_rate=None,
    last_job_assigned_at=None,
    now=None,
):
    return (
        provider_base_dispatch_score(
            rating=rating,
            response_minutes=response_minutes,
            acceptance_rate=acceptance_rate,
            completion_rate=completion_rate,
        )
        + provider_runtime_dispatch_score(
            distance_km,
            last_job_assigned_at=last_job_assigned_at,
            now=now,
        )
    )
