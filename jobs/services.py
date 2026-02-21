from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional

from django.db import IntegrityError, models, transaction
from django.db.models import Case, Count, Exists, F, IntegerField, OuterRef, Q, Value, When
from django.utils.dateparse import parse_date
from django.utils import timezone

from jobs.models import BroadcastAttemptStatus, Job, JobBroadcastAttempt, JobStatus

JOB_STATUS_FIELD = "job_status"
STATUS_POSTED = "posted"
RETRY_AFTER = timedelta(minutes=5)
MAX_ACTIVE_JOBS = 3
MARKETPLACE_RETRY_HOURS = 3
MARKETPLACE_MIN_LEAD_HOURS = 24
MARKETPLACE_EXPIRE_BUFFER_HOURS = 6
MARKETPLACE_BATCH_SIZE = 10
MARKETPLACE_MAX_ATTEMPTS = 6
MARKETPLACE_SEARCH_TIMEOUT_HOURS = 24
CLIENT_CONFIRMATION_TIMEOUT_MINUTES = 60
MARKETPLACE_ACTION_EXTEND_SEARCH_24H = "extend_search_24h"
MARKETPLACE_ACTION_EDIT_SCHEDULE_DATE = "edit_schedule_date"
MARKETPLACE_ACTION_SWITCH_TO_URGENT = "switch_to_urgent"
MARKETPLACE_ACTION_CANCEL_JOB = "cancel_job"

ScheduleFn = Callable[[int, timezone.datetime], None]


def default_schedule_fn(job_id: int, run_at):
    return None


@dataclass(frozen=True)
class ProcessResult:
    scheduled: bool
    reason: str


class MarketplaceDecisionConflict(Exception):
    pass


class MarketplaceAcceptConflict(Exception):
    pass


def _get_scheduled_datetime(job: Job):
    if not job.scheduled_date:
        return None
    scheduled_time = job.scheduled_start_time or time.min
    naive = datetime.combine(job.scheduled_date, scheduled_time)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _audit_tick(job_id: int, reason: str) -> None:
    Job.objects.filter(job_id=job_id).update(
        tick_attempts=models.F("tick_attempts") + 1,
        last_tick_attempt_at=timezone.now(),
        last_tick_attempt_reason=reason,
    )


def is_broadcastable(job: Job) -> bool:
    """
    Regla minima y estable:
    - Solo jobs en POSTED se pueden broadcast
    - Debe ser on_demand/urgent (si existen esos flags) o job_mode ON_DEMAND
    - No debe estar expirado
    """
    is_on_demand = (
        getattr(job, "is_on_demand", False)
        or getattr(job, "is_urgent", False)
        or getattr(job, "job_mode", None) == Job.JobMode.ON_DEMAND
    )

    expires_at = getattr(job, "expires_at", None)
    if expires_at is not None and expires_at <= timezone.now():
        return False

    return job.job_status == JobStatus.POSTED and bool(is_on_demand)


def is_on_demand_schedule_eligible(job: Job) -> bool:
    """
    Eligible para agendar procesamiento (tick / scheduler):
    - broadcastable
    - y no esta en hold activo
    """
    if not is_broadcastable(job):
        return False

    hold_until = getattr(job, "hold_until", None)
    if hold_until is None:
        hold_until = getattr(job, "hold_expires_at", None)
    if hold_until is not None and hold_until > timezone.now():
        return False

    return True


def schedule_next_alert(job):
    now = timezone.now()

    # Si ya existe una alerta futura, no hacer nada
    if job.next_alert_at and job.next_alert_at > now:
        return False

    job.next_alert_at = now + timedelta(minutes=2)
    job.alert_attempts += 1
    job.save(update_fields=["next_alert_at", "alert_attempts"])
    return True


def should_broadcast(job):
    return is_broadcastable(job)


def get_broadcast_candidates_for_job(job, limit=10):
    """
    PASO 6.3.1 - Matching real (optimizado con EXISTS)

    Criterios:
      - Provider activo (si existe: is_active / status)
      - Provider ofrece job.service_type_id (ProviderServiceType)
      - Provider cubre zona (ProviderServiceArea) por:
          ciudad (city/cities/locality)
          postal (postal_code/postal_prefix/postal_codes)
          region o fallback a province
      - Orden deterministico + limit
    """
    from providers.models import Provider, ProviderServiceArea, ProviderServiceType

    qs = Provider.objects.all()

    if hasattr(Provider, "is_active"):
        qs = qs.filter(is_active=True)
    if hasattr(Provider, "status"):
        qs = qs.filter(status__in=["active", "approved"])

    pst = ProviderServiceType.objects.filter(
        provider_id=OuterRef("provider_id"),
        service_type_id=job.service_type_id,
    )
    if hasattr(ProviderServiceType, "is_active"):
        pst = pst.filter(is_active=True)

    qs = qs.annotate(_has_service=Exists(pst)).filter(_has_service=True)

    job_city = getattr(job, "city", None) or getattr(job, "address_city", None)
    job_postal = getattr(job, "postal_code", None) or getattr(job, "address_postal_code", None)
    job_region = getattr(job, "region", None) or getattr(job, "address_region", None)
    job_province = getattr(job, "province", None) or getattr(job, "address_province", None)

    area_q = Q()

    if job_city:
        if hasattr(ProviderServiceArea, "city"):
            area_q |= Q(city__iexact=job_city)
        if hasattr(ProviderServiceArea, "cities"):
            area_q |= Q(cities__icontains=job_city)
        if hasattr(ProviderServiceArea, "locality"):
            area_q |= Q(locality__iexact=job_city)

    if job_postal:
        job_postal_str = str(job_postal).strip()
        postal_prefix = job_postal_str[:3]
        if hasattr(ProviderServiceArea, "postal_code"):
            area_q |= Q(postal_code__iexact=job_postal_str)
        if hasattr(ProviderServiceArea, "postal_prefix"):
            area_q |= Q(postal_prefix__iexact=postal_prefix)
        if hasattr(ProviderServiceArea, "postal_codes"):
            area_q |= Q(postal_codes__icontains=job_postal_str)

    if job_region and hasattr(ProviderServiceArea, "region"):
        area_q |= Q(region__iexact=job_region)
    if job_province and hasattr(ProviderServiceArea, "province"):
        area_q |= Q(province__iexact=job_province)

    if area_q:
        psa = ProviderServiceArea.objects.filter(
            provider_id=OuterRef("provider_id")
        ).filter(area_q)
        qs = qs.annotate(_in_area=Exists(psa)).filter(_in_area=True)
    else:
        qs = qs.annotate(_in_area=Value(True))

    if job_city:
        city_q = Q()
        if hasattr(ProviderServiceArea, "city"):
            city_q |= Q(city__iexact=job_city)
        if hasattr(ProviderServiceArea, "cities"):
            city_q |= Q(cities__icontains=job_city)
        if hasattr(ProviderServiceArea, "locality"):
            city_q |= Q(locality__iexact=job_city)

        psa_city = ProviderServiceArea.objects.filter(
            provider_id=OuterRef("provider_id"),
        ).filter(city_q)

        if hasattr(ProviderServiceArea, "is_active"):
            psa_city = psa_city.filter(is_active=True)

        qs = qs.annotate(_city_match=Exists(psa_city))
    else:
        qs = qs.annotate(_city_match=Value(False))

    qs = qs.annotate(
        _score=Case(
            When(_city_match=True, then=Value(0)),
            When(_in_area=True, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
    )

    COOLDOWN_MINUTES = 10
    cooldown_threshold = timezone.now() - timedelta(minutes=COOLDOWN_MINUTES)

    recent_attempts = JobBroadcastAttempt.objects.filter(
        provider_id=OuterRef("provider_id"),
        created_at__gte=cooldown_threshold,
    )

    qs = qs.annotate(
        _has_recent_attempt=Exists(recent_attempts)
    )

    qs = qs.annotate(
        _cooldown_penalty=Case(
            When(_has_recent_attempt=True, then=Value(2)),
            default=Value(0),
            output_field=IntegerField(),
        )
    )

    qs = qs.annotate(
        _final_score=F("_score") + F("_cooldown_penalty")
    )

    active_statuses = [
        Job.JobStatus.POSTED,
        Job.JobStatus.HOLD,
        Job.JobStatus.PENDING_PROVIDER_CONFIRMATION,
        Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
        Job.JobStatus.ASSIGNED,
        Job.JobStatus.IN_PROGRESS,
    ]
    qs = qs.annotate(
        _active_jobs_count=Count(
            "selected_jobs",
            filter=Q(selected_jobs__job_status__in=active_statuses),
            distinct=True,
        )
    )
    qs = qs.filter(_active_jobs_count__lt=MAX_ACTIVE_JOBS)

    qs = qs.order_by("_final_score", "provider_id")
    return list(qs.values_list("provider_id", flat=True)[:limit])


def record_broadcast_attempt(*, job_id: int, provider_id: int, status: str, detail: str | None = None) -> bool:
    """
    Crea un intento por provider/job. Si ya existe, retorna False.
    """
    try:
        with transaction.atomic():
            JobBroadcastAttempt.objects.create(
                job_id=job_id,
                provider_id=provider_id,
                status=status,
                detail=detail,
            )
        return True
    except IntegrityError:
        return False


def process_on_demand_job(job_or_id, *, schedule_fn: Optional[ScheduleFn] = None) -> ProcessResult:
    """
    Garantias:
    - Concurrencia segura con row lock.
    - Recovery-safe: si el scheduler falla, permite reintento luego de RETRY_AFTER.
    """
    schedule_fn = schedule_fn or default_schedule_fn
    now = timezone.now()
    job_id = job_or_id.pk if isinstance(job_or_id, Job) else int(job_or_id)

    def _result(scheduled: bool, reason: str) -> ProcessResult:
        _audit_tick(job_id, reason)
        return ProcessResult(scheduled=scheduled, reason=reason)

    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)

        if not is_on_demand_schedule_eligible(job):
            return _result(scheduled=False, reason="not_eligible")

        status = getattr(job, JOB_STATUS_FIELD, None)
        if status is not None and str(status).lower() != STATUS_POSTED:
            return _result(scheduled=False, reason="status_not_posted")

        if job.on_demand_tick_dispatched_at is not None:
            return _result(scheduled=False, reason="already_dispatched")

        if job.on_demand_tick_scheduled_at is not None:
            age = now - job.on_demand_tick_scheduled_at
            if age < RETRY_AFTER:
                return _result(
                    scheduled=False,
                    reason="already_scheduled_wait_retry_window",
                )
            job.on_demand_tick_scheduled_at = now
            job.save(update_fields=["on_demand_tick_scheduled_at"])
        else:
            job.on_demand_tick_scheduled_at = now
            job.save(update_fields=["on_demand_tick_scheduled_at"])

        MAX_ALERT_ATTEMPTS = 10
        if job.alert_attempts >= MAX_ALERT_ATTEMPTS:
            job.job_status = Job.JobStatus.EXPIRED
            job.next_alert_at = None
            job.save(update_fields=["job_status", "next_alert_at"])
            return _result(scheduled=False, reason="max_attempts_reached")

        schedule_next_alert(job)

    run_at = now + timedelta(seconds=0)
    try:
        schedule_fn(job_id, run_at)
    except Exception:
        return _result(scheduled=False, reason="schedule_fn_failed")

    Job.objects.filter(
        job_id=job_id,
        on_demand_tick_dispatched_at__isnull=True,
    ).update(on_demand_tick_dispatched_at=timezone.now())

    return _result(scheduled=True, reason="dispatched_once")


def process_marketplace_job(job_or_id) -> tuple[str, int, int]:
    now = timezone.now()
    job_id = job_or_id.pk if isinstance(job_or_id, Job) else int(job_or_id)

    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)

        if getattr(job, "job_mode", None) != Job.JobMode.SCHEDULED:
            return ("skip_not_scheduled", 0, 0)

        allowed_marketplace_statuses = (
            Job.JobStatus.POSTED,
            Job.JobStatus.WAITING_PROVIDER_RESPONSE,
        )
        if getattr(job, "job_status", None) not in allowed_marketplace_statuses:
            return ("skip_not_marketplace_status", 0, 0)

        scheduled_at = _get_scheduled_datetime(job)
        if scheduled_at is None:
            return ("skip_missing_scheduled_date", 0, 0)

        if scheduled_at < (now + timedelta(hours=MARKETPLACE_MIN_LEAD_HOURS)):
            return ("skip_less_than_24h", 0, 0)

        if not job.marketplace_expires_at:
            job.marketplace_expires_at = scheduled_at - timedelta(hours=MARKETPLACE_EXPIRE_BUFFER_HOURS)
            job.save(update_fields=["marketplace_expires_at"])

        if job.marketplace_search_started_at:
            search_deadline = job.marketplace_search_started_at + timedelta(
                hours=MARKETPLACE_SEARCH_TIMEOUT_HOURS
            )
            if now >= search_deadline:
                Job.objects.filter(job_id=job.job_id).update(
                    job_status=Job.JobStatus.PENDING_CLIENT_DECISION,
                    next_marketplace_alert_at=None,
                )
                return ("pending_client_decision_timeout_24h", 0, 0)

        if now >= job.marketplace_expires_at:
            job.job_status = Job.JobStatus.EXPIRED
            job.next_marketplace_alert_at = None
            job.save(update_fields=["job_status", "next_marketplace_alert_at"])
            return ("expired_no_provider", 0, 0)

        due = (job.next_marketplace_alert_at is None) or (job.next_marketplace_alert_at <= now)
        if not due:
            return ("not_due", 0, 0)

        if job.marketplace_attempts >= MARKETPLACE_MAX_ATTEMPTS:
            job.job_status = Job.JobStatus.EXPIRED
            job.next_marketplace_alert_at = None
            job.save(update_fields=["job_status", "next_marketplace_alert_at"])
            return ("expired_max_attempts", 0, 0)

        attempt_number = int(job.marketplace_attempts or 0) + 1
        job.next_marketplace_alert_at = now + timedelta(hours=MARKETPLACE_RETRY_HOURS)

        desired_pool = max(attempt_number * MARKETPLACE_BATCH_SIZE * 3, MARKETPLACE_BATCH_SIZE)
        provider_ids_ranked = get_broadcast_candidates_for_job(job, limit=desired_pool)

        already_attempted = set(
            JobBroadcastAttempt.objects.filter(job_id=job.job_id).values_list("provider_id", flat=True)
        )
        wave = [pid for pid in provider_ids_ranked if pid not in already_attempted][:MARKETPLACE_BATCH_SIZE]

        if not wave:
            Job.objects.filter(pk=job.job_id).update(
                marketplace_attempts=F("marketplace_attempts") + 1,
                next_marketplace_alert_at=job.next_marketplace_alert_at,
                marketplace_expires_at=job.marketplace_expires_at,
            )
            return ("due_no_new_candidates", 0, 0)

        created_count = 0
        skipped_count = 0
        for provider_id in wave:
            created = record_broadcast_attempt(
                job_id=job.job_id,
                provider_id=provider_id,
                status=BroadcastAttemptStatus.SENT,
                detail=f"marketplace_attempt={attempt_number}",
            )
            if created:
                created_count += 1
            else:
                skipped_count += 1

        update_kwargs = {
            "marketplace_attempts": F("marketplace_attempts") + 1,
            "next_marketplace_alert_at": job.next_marketplace_alert_at,
            "marketplace_expires_at": job.marketplace_expires_at,
        }
        if created_count > 0 and not job.marketplace_search_started_at:
            update_kwargs["marketplace_search_started_at"] = now
            update_kwargs["job_status"] = Job.JobStatus.WAITING_PROVIDER_RESPONSE

        Job.objects.filter(pk=job.job_id).update(
            **update_kwargs,
        )

    return ("dispatched_wave", created_count, skipped_count)


def _coerce_marketplace_date(value) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        parsed = parse_date(value)
        if parsed is not None:
            return parsed
    raise MarketplaceDecisionConflict("INVALID_SCHEDULED_DATE")


def _validate_marketplace_lead_time(*, scheduled_date: date, scheduled_start_time, now) -> datetime:
    scheduled_time = scheduled_start_time or time.min
    scheduled_at = timezone.make_aware(
        datetime.combine(scheduled_date, scheduled_time),
        timezone.get_current_timezone(),
    )
    if scheduled_at < (now + timedelta(hours=MARKETPLACE_MIN_LEAD_HOURS)):
        raise MarketplaceDecisionConflict("SCHEDULE_LESS_THAN_24H")
    return scheduled_at


def apply_client_marketplace_decision(
    *,
    job_id: int,
    action: str,
    payload: Optional[dict] = None,
    now=None,
) -> str:
    payload = payload or {}
    now = now or timezone.now()

    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)

        if action == MARKETPLACE_ACTION_EXTEND_SEARCH_24H:
            if job.job_status != Job.JobStatus.PENDING_CLIENT_DECISION:
                raise MarketplaceDecisionConflict("INVALID_STATUS_FOR_EXTEND")
            if job.job_mode != Job.JobMode.SCHEDULED or not job.scheduled_date:
                raise MarketplaceDecisionConflict("INVALID_JOB_MODE_FOR_MARKETPLACE")

            _validate_marketplace_lead_time(
                scheduled_date=job.scheduled_date,
                scheduled_start_time=job.scheduled_start_time,
                now=now,
            )

            Job.objects.filter(pk=job.job_id).update(
                job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
                marketplace_search_started_at=now,
                next_marketplace_alert_at=now,
            )
            return "extended_search"

        if action == MARKETPLACE_ACTION_EDIT_SCHEDULE_DATE:
            allowed_statuses = (
                Job.JobStatus.PENDING_CLIENT_DECISION,
                Job.JobStatus.POSTED,
                Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            )
            if job.job_status not in allowed_statuses:
                raise MarketplaceDecisionConflict("INVALID_STATUS_FOR_EDIT_SCHEDULE")
            if job.job_mode != Job.JobMode.SCHEDULED:
                raise MarketplaceDecisionConflict("INVALID_JOB_MODE_FOR_MARKETPLACE")

            new_date = _coerce_marketplace_date(payload.get("scheduled_date"))
            if new_date <= timezone.localdate():
                raise MarketplaceDecisionConflict("INVALID_SCHEDULED_DATE")

            scheduled_at = _validate_marketplace_lead_time(
                scheduled_date=new_date,
                scheduled_start_time=job.scheduled_start_time,
                now=now,
            )

            Job.objects.filter(pk=job.job_id).update(
                scheduled_date=new_date,
                job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
                marketplace_search_started_at=now,
                next_marketplace_alert_at=now,
                marketplace_expires_at=scheduled_at - timedelta(hours=MARKETPLACE_EXPIRE_BUFFER_HOURS),
            )
            return "schedule_updated"

        if action == MARKETPLACE_ACTION_SWITCH_TO_URGENT:
            if job.job_status != Job.JobStatus.PENDING_CLIENT_DECISION:
                raise MarketplaceDecisionConflict("INVALID_STATUS_FOR_SWITCH_TO_URGENT")

            Job.objects.filter(pk=job.job_id).update(
                job_mode=Job.JobMode.ON_DEMAND,
                scheduled_date=None,
                job_status=Job.JobStatus.POSTED,
                is_asap=True,
                next_marketplace_alert_at=None,
                marketplace_search_started_at=None,
                marketplace_expires_at=None,
                marketplace_attempts=0,
            )
            return "switched_to_urgent"

        if action == MARKETPLACE_ACTION_CANCEL_JOB:
            allowed_statuses = (
                Job.JobStatus.PENDING_CLIENT_DECISION,
                Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            )
            if job.job_status not in allowed_statuses:
                raise MarketplaceDecisionConflict("INVALID_STATUS_FOR_CANCEL")

            Job.objects.filter(pk=job.job_id).update(
                job_status=Job.JobStatus.CANCELLED,
                next_marketplace_alert_at=None,
                marketplace_search_started_at=None,
                client_confirmation_started_at=None,
                selected_provider_id=None,
            )
            return "cancelled"

        raise MarketplaceDecisionConflict("INVALID_ACTION")


def accept_marketplace_offer(*, job_id: int, provider_id: int, now=None) -> str:
    now = now or timezone.now()

    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)

        if job.job_mode != Job.JobMode.SCHEDULED:
            raise MarketplaceAcceptConflict("INVALID_JOB_MODE_FOR_MARKETPLACE_ACCEPT")

        if job.job_status == Job.JobStatus.ASSIGNED:
            raise MarketplaceAcceptConflict("job_already_assigned")

        if (
            job.job_status == Job.JobStatus.PENDING_CLIENT_CONFIRMATION
            and job.selected_provider_id == provider_id
        ):
            return "already_accepted_waiting_client"

        if job.job_status != Job.JobStatus.WAITING_PROVIDER_RESPONSE:
            raise MarketplaceAcceptConflict("INVALID_STATUS_FOR_MARKETPLACE_ACCEPT")

        if not job.marketplace_search_started_at:
            raise MarketplaceAcceptConflict("MISSING_MARKETPLACE_SEARCH_WINDOW")

        search_deadline = job.marketplace_search_started_at + timedelta(
            hours=MARKETPLACE_SEARCH_TIMEOUT_HOURS
        )
        if now >= search_deadline:
            raise MarketplaceAcceptConflict("MARKETPLACE_SEARCH_TIMEOUT")

        attempt = (
            JobBroadcastAttempt.objects.filter(job_id=job.job_id, provider_id=provider_id)
            .order_by("-created_at")
            .first()
        )
        if not attempt:
            raise MarketplaceAcceptConflict("BROADCAST_ATTEMPT_NOT_FOUND")

        if attempt.created_at < job.marketplace_search_started_at:
            raise MarketplaceAcceptConflict("STALE_BROADCAST_ATTEMPT")

        Job.objects.filter(pk=job.job_id).update(
            selected_provider_id=provider_id,
            job_status=Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            client_confirmation_started_at=now,
            next_marketplace_alert_at=None,
        )

    return "accepted_waiting_client"


def process_marketplace_client_confirmation_timeout(job_or_id, *, now=None) -> tuple[str, int]:
    now = now or timezone.now()
    job_id = job_or_id.pk if isinstance(job_or_id, Job) else int(job_or_id)

    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)

        if job.job_status != Job.JobStatus.PENDING_CLIENT_CONFIRMATION:
            return ("skip_not_pending_client_confirmation", 0)

        if not job.client_confirmation_started_at:
            return ("skip_missing_client_confirmation_started_at", 0)

        deadline = job.client_confirmation_started_at + timedelta(
            minutes=CLIENT_CONFIRMATION_TIMEOUT_MINUTES
        )
        if now < deadline:
            return ("not_due_client_confirmation_timeout", 0)

        search_deadline = None
        if job.marketplace_search_started_at:
            search_deadline = job.marketplace_search_started_at + timedelta(
                hours=MARKETPLACE_SEARCH_TIMEOUT_HOURS
            )
        if search_deadline is not None and now >= search_deadline:
            Job.objects.filter(pk=job.job_id).update(
                job_status=Job.JobStatus.PENDING_CLIENT_DECISION,
                selected_provider_id=None,
                client_confirmation_started_at=None,
                next_marketplace_alert_at=None,
            )
            return ("timeout_to_pending_client_decision", 1)

        Job.objects.filter(pk=job.job_id).update(
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            selected_provider_id=None,
            client_confirmation_started_at=None,
            next_marketplace_alert_at=now,
        )
    return ("timeout_reopened_marketplace", 1)


def confirm_marketplace_provider(*, job_id: int, now=None) -> str:
    now = now or timezone.now()

    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)

        if job.job_status == Job.JobStatus.ASSIGNED:
            return "already_assigned"

        if job.job_status != Job.JobStatus.PENDING_CLIENT_CONFIRMATION:
            raise MarketplaceDecisionConflict("INVALID_STATUS_FOR_CLIENT_CONFIRM")

        if not job.selected_provider_id:
            raise MarketplaceDecisionConflict("MISSING_SELECTED_PROVIDER")

        if not job.client_confirmation_started_at:
            raise MarketplaceDecisionConflict("MISSING_CLIENT_CONFIRMATION_WINDOW")

        deadline = job.client_confirmation_started_at + timedelta(
            minutes=CLIENT_CONFIRMATION_TIMEOUT_MINUTES
        )
        if now >= deadline:
            raise MarketplaceDecisionConflict("CLIENT_CONFIRMATION_TIMEOUT")

        Job.objects.filter(pk=job.job_id).update(
            job_status=Job.JobStatus.ASSIGNED,
            next_marketplace_alert_at=None,
            marketplace_search_started_at=None,
            client_confirmation_started_at=None,
        )

    return "confirmed"
