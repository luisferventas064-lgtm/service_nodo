from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Optional

from django.db import IntegrityError, models, transaction
from django.db.models import Case, Count, Exists, F, IntegerField, OuterRef, Q, Value, When
from django.utils import timezone

from jobs.models import Job, JobBroadcastAttempt, JobStatus

JOB_STATUS_FIELD = "job_status"
STATUS_POSTED = "posted"
RETRY_AFTER = timedelta(minutes=5)
MAX_ACTIVE_JOBS = 3

ScheduleFn = Callable[[int, timezone.datetime], None]


def default_schedule_fn(job_id: int, run_at):
    return None


@dataclass(frozen=True)
class ProcessResult:
    scheduled: bool
    reason: str


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
