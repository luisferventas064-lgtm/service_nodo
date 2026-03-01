from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from jobs.models import Job, JobDispute
from providers.models import Provider

QUALITY_WARNING_THRESHOLD = 3
QUALITY_RESTRICTION_LEVEL_1_THRESHOLD = 5
QUALITY_RESTRICTION_LEVEL_2_THRESHOLD = 6
QUALITY_RESTRICTION_LEVEL_3_THRESHOLD = 8


@dataclass(frozen=True)
class QualityEnforcementResult:
    provider: Provider
    warning_activated: bool
    recent_disputes_last_12m: int


def apply_dispute_loss_penalty(provider_id: int) -> None:
    provider = Provider.objects.select_for_update().get(pk=provider_id)
    provider.disputes_lost_count += 1
    provider.save(update_fields=["disputes_lost_count", "updated_at"])


def enforce_provider_quality_policy(provider_id: int) -> QualityEnforcementResult:
    provider = Provider.objects.select_for_update().get(pk=provider_id)
    now = timezone.now()
    cutoff = now - timedelta(days=365)
    recent_disputes_last_12m = JobDispute.objects.filter(
        provider_id=provider_id,
        status=JobDispute.DisputeStatus.RESOLVED,
        job__cancel_reason=Job.CancelReason.DISPUTE_APPROVED,
        resolved_at__gte=cutoff,
    ).count()

    previous_warning_active = provider.quality_warning_active
    provider.quality_warning_active = (
        recent_disputes_last_12m >= QUALITY_WARNING_THRESHOLD
    )

    if recent_disputes_last_12m >= QUALITY_RESTRICTION_LEVEL_3_THRESHOLD:
        restriction_days = 90
    elif recent_disputes_last_12m >= QUALITY_RESTRICTION_LEVEL_2_THRESHOLD:
        restriction_days = 60
    elif recent_disputes_last_12m >= QUALITY_RESTRICTION_LEVEL_1_THRESHOLD:
        restriction_days = 30
    else:
        restriction_days = 0

    if restriction_days > 0:
        provider.restricted_until = now + timedelta(days=restriction_days)
    else:
        provider.restricted_until = None

    provider.save(update_fields=["quality_warning_active", "restricted_until", "updated_at"])

    return QualityEnforcementResult(
        provider=provider,
        warning_activated=(
            not previous_warning_active and provider.quality_warning_active
        ),
        recent_disputes_last_12m=recent_disputes_last_12m,
    )
