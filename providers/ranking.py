from __future__ import annotations

from math import log10
from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from providers.utils_ranking import provider_base_dispatch_score

RELIABILITY_WEIGHT = 0.35
PROXIMITY_WEIGHT = 0.20
RATING_WEIGHT = 0.20
RESPONSE_WEIGHT = 0.15
EXPERIENCE_WEIGHT = 0.10
EXPERIENCE_LOG_SCALE = 2.0


def _clamp_score(value) -> float:
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError):
        numeric = 0.0
    return max(0.0, min(numeric, 1.0))


def _default_metrics(provider=None):
    jobs_completed = int(getattr(provider, "completed_jobs_count", 0) or 0)
    jobs_cancelled = int(getattr(provider, "cancelled_jobs_count", 0) or 0)
    return SimpleNamespace(
        offers_received_count=0,
        offers_accepted_count=0,
        jobs_completed=jobs_completed,
        jobs_accepted=jobs_completed + jobs_cancelled,
        jobs_cancelled=jobs_cancelled,
        avg_response_time=0.0,
        acceptance_rate=0.0,
        completion_rate=0.0,
        experience_score=0.0,
        operational_score=0.0,
        response_score=0.0,
    )


def _resolve_metrics(provider, metrics=None):
    if metrics is not None:
        return metrics
    try:
        return provider.metrics
    except ObjectDoesNotExist:
        return _default_metrics(provider)


def calculate_acceptance_rate(metrics) -> float:
    offers_received = float(getattr(metrics, "offers_received_count", 0) or 0)
    offers_accepted = float(getattr(metrics, "offers_accepted_count", 0) or 0)
    if offers_received > 0:
        return round(_clamp_score(offers_accepted / offers_received), 4)

    completed = float(metrics.jobs_completed or 0)
    cancelled = float(metrics.jobs_cancelled or 0)
    total_terminal_jobs = completed + cancelled
    if total_terminal_jobs <= 0:
        return 0.0
    return round(_clamp_score(completed / total_terminal_jobs), 4)


def calculate_completion_rate(metrics) -> float:
    accepted = float(metrics.jobs_accepted or 0)
    if accepted <= 0:
        return 0.0
    return round(_clamp_score((metrics.jobs_completed or 0) / accepted), 4)


def calculate_experience_score(metrics) -> float:
    completed = float(getattr(metrics, "jobs_completed", 0) or 0)
    if completed <= 0:
        return 0.0
    return round(_clamp_score(log10(completed + 1.0) / EXPERIENCE_LOG_SCALE), 4)


def calculate_response_score(metrics) -> float:
    average_response_time = float(getattr(metrics, "avg_response_time", 0.0) or 0.0)
    if average_response_time <= 0:
        return 0.0
    return round(_clamp_score(1.0 - (average_response_time / 60.0)), 4)


def calculate_operational_score(provider, metrics) -> float:
    base_score = (
        (calculate_acceptance_rate(metrics) * 0.5)
        + (calculate_completion_rate(metrics) * 0.5)
    )

    if getattr(provider, "quality_warning_active", False):
        base_score -= 0.15

    restricted_until = getattr(provider, "restricted_until", None)
    if restricted_until and restricted_until > timezone.now():
        base_score -= 0.50

    return round(_clamp_score(base_score), 4)


def hydrate_provider_metrics(provider, metrics=None):
    metrics = _resolve_metrics(provider, metrics)
    metrics.acceptance_rate = calculate_acceptance_rate(metrics)
    metrics.completion_rate = calculate_completion_rate(metrics)
    metrics.experience_score = calculate_experience_score(metrics)
    metrics.response_score = calculate_response_score(metrics)
    metrics.operational_score = calculate_operational_score(provider, metrics)
    return metrics


def calculate_hybrid_score(provider, metrics=None) -> float:
    metrics = hydrate_provider_metrics(provider, metrics)
    reliability = _clamp_score(metrics.operational_score)
    proximity = _clamp_score(getattr(provider, "distance_score", 0.0))
    rating = _clamp_score((getattr(provider, "avg_rating", 0) or 0) / 5)
    response = _clamp_score(metrics.response_score)
    experience = _clamp_score(metrics.experience_score)

    score = (
        (reliability * RELIABILITY_WEIGHT)
        + (proximity * PROXIMITY_WEIGHT)
        + (rating * RATING_WEIGHT)
        + (response * RESPONSE_WEIGHT)
        + (experience * EXPERIENCE_WEIGHT)
    )
    return round(_clamp_score(score), 4)


def calculate_base_dispatch_score(provider, metrics=None) -> float:
    metrics = hydrate_provider_metrics(provider, metrics)
    return round(
        _clamp_score(
            provider_base_dispatch_score(
                rating=getattr(provider, "avg_rating", 0) or 0,
                response_minutes=getattr(metrics, "avg_response_time", 0.0),
                acceptance_rate=getattr(metrics, "acceptance_rate", 0.0),
                completion_rate=getattr(metrics, "completion_rate", 0.0),
            )
        ),
        4,
    )


def hydrate_provider_ranking_fields(provider, metrics=None):
    metrics = hydrate_provider_metrics(provider, metrics)
    provider.acceptance_rate = round(metrics.acceptance_rate * 100, 2)
    provider.hybrid_score = calculate_hybrid_score(provider, metrics)
    provider.base_dispatch_score = calculate_base_dispatch_score(provider, metrics)
    return provider
