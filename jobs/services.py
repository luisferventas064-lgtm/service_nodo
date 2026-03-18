from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import random
from typing import Callable, Optional

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import (
    Case,
    Count,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    OuterRef,
    Q,
    Value,
    When,
)
from django.db.models.functions import Cast
from django.utils.dateparse import parse_date
from django.utils import timezone

from assignments.models import JobAssignment
from clients.lines import ensure_client_base_line
from clients.lines_fee import ensure_client_fee_line
from clients.models import ClientTicket
from clients.ticketing import ensure_client_ticket, finalize_client_ticket
from clients.totals import recalc_client_ticket_totals
from jobs.events import create_job_event
from jobs.evidence import try_write_job_evidence_json
from jobs.ledger import finalize_platform_ledger_for_job
from jobs.metrics import log_job_event
from jobs.models import (
    BroadcastAttemptStatus,
    Job,
    JobBroadcastAttempt,
    JobDispute,
    JobEvent,
    JobProviderExclusion,
    JobStatus,
)
from jobs.observability import log_assignment_event, log_marketplace_timeout
from jobs.services_fee import recompute_on_demand_fee_for_open_tickets
from jobs.services_pricing_snapshot import (
    job_snapshot_currency,
    job_snapshot_subtotal_cents,
    job_snapshot_total_cents,
)
from jobs.services_state_transitions import (
    reactivate_assignment_legacy,
    transition_assignment_status,
    transition_job_status,
)
from notifications.services import (
    send_dispute_resolution_email,
    send_quality_warning_email,
)
from providers.lines import ensure_provider_base_line
from providers.lines_fee import ensure_provider_fee_line
from providers.models import Provider
from providers.services import apply_dispute_loss_penalty, enforce_provider_quality_policy
from providers.ticketing import ensure_provider_ticket, finalize_provider_ticket
from providers.totals import recalc_provider_ticket_totals

JOB_STATUS_FIELD = "job_status"
STATUS_POSTED = "posted"
RETRY_AFTER = timedelta(minutes=5)
MAX_ACTIVE_JOBS = 3
LOAD_WEIGHT = 2.0
MARKETPLACE_RETRY_HOURS = 3
MARKETPLACE_MIN_LEAD_HOURS = 24
MARKETPLACE_EXPIRE_BUFFER_HOURS = 6
BROADCAST_RADIUS_KM = 30.0
MARKETPLACE_BATCH_SIZE = 10
MIN_DYNAMIC_WAVE_SIZE = 2
DISPATCH_SCORE_GAP_STEP = 0.05
DISPATCH_SCORE_GAP_CAP = 0.25
SOFT_RANDOM_BONUS_MAX = 0.02
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


@dataclass(frozen=True)
class BroadcastCandidate:
    provider_id: int
    dynamic_score: float | None
    dispatch_score: float | None
    distance_km: float | None
    area_score: int
    cooldown_penalty: int
    load_penalty: float


class MarketplaceDecisionConflict(Exception):
    pass


class MarketplaceAcceptConflict(Exception):
    pass


class ProviderAcceptConflict(Exception):
    pass


def _normalize_country_code(country: str | None) -> str:
    if not country:
        return ""
    value = str(country).strip().upper()
    if value in {"CANADA", "CA"}:
        return "CA"
    if value in {"UNITED STATES", "USA", "US"}:
        return "US"
    if len(value) == 2:
        return value
    return value[:2]


def _build_tax_region_code(job: Job) -> str:
    country_code = _normalize_country_code(getattr(job, "country", ""))
    province_code = str(getattr(job, "province", "") or "").strip().upper()
    if country_code and province_code:
        return f"{country_code}-{province_code}"
    return country_code or province_code


def _client_ticket_snapshot_for_finalization(
    *,
    job: Job,
    client_id: int,
    job_id: int,
    fallback_currency: str,
    fallback_tax_region_code: str,
) -> tuple[int, int, int, str, str]:
    ticket = (
        ClientTicket.objects.filter(
            client_id=client_id,
            ref_type="job",
            ref_id=job_id,
        )
        .order_by("-client_ticket_id")
        .first()
    )
    if not ticket:
        subtotal_cents = job_snapshot_subtotal_cents(job)
        total_cents = job_snapshot_total_cents(job)
        currency = job_snapshot_currency(job)
        return (
            subtotal_cents,
            0,
            total_cents,
            currency or fallback_currency,
            fallback_tax_region_code,
        )

    if ticket.status != ClientTicket.Status.FINALIZED:
        recalc_client_ticket_totals(ticket.pk)
        ticket.refresh_from_db()

    return (
        int(ticket.subtotal_cents or 0),
        int(ticket.tax_cents or 0),
        int(ticket.total_cents or 0),
        ticket.currency or fallback_currency,
        ticket.tax_region_code or fallback_tax_region_code,
    )


def _ensure_job_estimate_tickets_from_snapshot(
    *,
    job: Job,
    provider_id: int,
    tax_region_code: str,
):
    subtotal_cents = job_snapshot_subtotal_cents(job)
    currency = job_snapshot_currency(job)

    pt = ensure_provider_ticket(
        provider_id=provider_id,
        ref_type="job",
        ref_id=job.job_id,
        stage="estimate",
        status="open",
        subtotal_cents=subtotal_cents,
        tax_cents=0,
        total_cents=subtotal_cents,
        currency=currency,
        tax_region_code=tax_region_code,
    )
    if pt.stage == "estimate" and pt.status == "open":
        ensure_provider_base_line(
            pt.pk,
            description="Service (estimate)",
            unit_price_cents=subtotal_cents,
            tax_cents=pt.tax_cents or 0,
            tax_region_code=pt.tax_region_code or "",
            tax_code="",
        )

    ct = None
    if job.client_id:
        ct = ensure_client_ticket(
            client_id=job.client_id,
            ref_type="job",
            ref_id=job.job_id,
            stage="estimate",
            status="open",
            subtotal_cents=subtotal_cents,
            tax_cents=0,
            total_cents=subtotal_cents,
            currency=currency,
            tax_region_code=tax_region_code,
        )
        if ct.stage == "estimate" and ct.status == "open":
            ensure_client_base_line(
                ct.pk,
                description="Service (estimate)",
                unit_price_cents=subtotal_cents,
                tax_cents=ct.tax_cents or 0,
                tax_region_code=ct.tax_region_code or "",
                tax_code="",
            )
        if (
            job.job_mode == Job.JobMode.ON_DEMAND
            and pt.status == "open"
            and ct.status == "open"
        ):
            ensure_provider_fee_line(pt.pk, amount_cents=0)
            ensure_client_fee_line(ct.pk, amount_cents=0)
            recompute_on_demand_fee_for_open_tickets(pt.pk, ct.pk)

    return pt, ct


def _resolve_active_provider_id_for_job(job: Job) -> int | None:
    from assignments.models import JobAssignment

    active_assignment = JobAssignment.objects.filter(
        job_id=job.job_id,
        is_active=True,
    ).first()
    if active_assignment:
        return active_assignment.provider_id
    return job.selected_provider_id


def _deactivate_active_assignments_for_job(*, job: Job, actor_role: str, reason: str) -> int:
    active_assignments = list(
        JobAssignment.objects.filter(job_id=job.job_id, is_active=True).order_by("assignment_id")
    )
    deactivated = 0
    for assignment in active_assignments:
        transition_assignment_status(
            assignment,
            "cancelled",
            actor=actor_role,
            reason=reason,
        )
        log_assignment_event(
            job.job_id,
            assignment.assignment_id,
            "cleared",
            assignment.provider_id,
        )
        deactivated += 1
    return deactivated


def _activate_marketplace_assignment_for_job(*, job_id: int, provider_id: int) -> int:
    from assignments.models import JobAssignment
    from providers.models import Provider

    JobAssignment.objects.select_for_update().filter(job_id=job_id).exists()

    existing_same = JobAssignment.objects.filter(
        job_id=job_id,
        provider_id=provider_id,
        is_active=True,
    ).first()
    if existing_same:
        log_assignment_event(job_id, existing_same.assignment_id, "already_active", provider_id)
        return existing_same.assignment_id

    active_other = (
        JobAssignment.objects.filter(job_id=job_id, is_active=True)
        .exclude(provider_id=provider_id)
        .first()
    )
    if active_other:
        raise MarketplaceDecisionConflict("ACTIVE_ASSIGNMENT_OTHER_PROVIDER")

    assignment = JobAssignment.objects.filter(
        job_id=job_id,
        provider_id=provider_id,
    ).first()
    if assignment:
        if not assignment.is_active or assignment.assignment_status != "assigned":
            assigned_at = timezone.now()
            reactivate_assignment_legacy(
                assignment,
                actor=JobEvent.ActorRole.SYSTEM,
                reason="marketplace_assignment_reactivation",
            )
            log_assignment_event(job_id, assignment.assignment_id, "reactivated", provider_id)
            Provider.objects.filter(provider_id=provider_id).update(
                last_job_assigned_at=assigned_at
            )
        else:
            log_assignment_event(job_id, assignment.assignment_id, "already_active", provider_id)
        return assignment.assignment_id

    try:
        assigned_at = timezone.now()
        assignment = JobAssignment.objects.create(
            job_id=job_id,
            provider_id=provider_id,
            is_active=True,
            assignment_status="assigned",
        )
        log_assignment_event(job_id, assignment.assignment_id, "created", provider_id)
        Provider.objects.filter(provider_id=provider_id).update(last_job_assigned_at=assigned_at)
    except IntegrityError as exc:
        raise MarketplaceDecisionConflict("ASSIGNMENT_ACTIVATION_CONFLICT") from exc

    return assignment.assignment_id


def _ensure_assignment_fee_off(*, assignment_id: int) -> None:
    from assignments.models import AssignmentFee
    from assignments.services import compute_assignment_fee_off

    fee_data = compute_assignment_fee_off()
    AssignmentFee.objects.get_or_create(
        assignment_id=assignment_id,
        defaults={
            "payer": fee_data["payer"],
            "model": fee_data["model"],
            "status": fee_data["status"],
            "amount_cents": fee_data["amount_cents"],
            "currency": fee_data["currency"],
        },
    )


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


def dispatch_soft_random_bonus(*, job_id: int, provider_id: int, attempt_number: int) -> float:
    stable_attempt_number = max(int(attempt_number or 1), 1)
    rng = random.Random(f"dispatch:{job_id}:{provider_id}:{stable_attempt_number}")
    return rng.uniform(0, SOFT_RANDOM_BONUS_MAX)


def rank_broadcast_candidates_for_job(job, limit=10, attempt_number: int | None = None):
    """
    PASO 6.3.1 - Matching real (optimizado con EXISTS)

    Criterios:
      - Provider activo (si existe: is_active / status)
      - Provider ofrece job.service_type_id (ProviderService)
      - Provider cubre zona (ProviderServiceArea) por:
          ciudad (city/cities/locality)
          postal (postal_code/postal_prefix/postal_codes)
          region o fallback a province
      - Orden deterministico + limit
    """
    from providers.models import Provider, ProviderLocation, ProviderService, ProviderServiceArea
    from providers.availability import effective_provider_availability_q
    from providers.utils_distance import haversine_distance_km, providers_within_radius
    from providers.utils_geo_grid import grid_window_for_radius
    from providers.utils_ranking import dispatch_score_from_base, provider_runtime_dispatch_score

    qs = Provider.objects.all()

    if hasattr(Provider, "is_active"):
        qs = qs.filter(is_active=True)
    if hasattr(Provider, "status"):
        qs = qs.filter(status__in=["active", "approved"])
    qs = qs.filter(effective_provider_availability_q(now=timezone.now()))

    if job.job_mode == Job.JobMode.ON_DEMAND:
        if hasattr(Provider, "accepts_urgent"):
            qs = qs.filter(accepts_urgent=True)
    elif job.job_mode == Job.JobMode.SCHEDULED:
        if hasattr(Provider, "accepts_scheduled"):
            qs = qs.filter(accepts_scheduled=True)

    pst = ProviderService.objects.filter(
        provider_id=OuterRef("provider_id"),
        service_type_id=job.service_type_id,
        is_active=True,
    )

    qs = qs.annotate(_has_service=Exists(pst)).filter(_has_service=True)

    excluded_providers = JobProviderExclusion.objects.filter(
        job_id=job.job_id,
        provider_id=OuterRef("provider_id"),
    )
    qs = qs.annotate(_job_excluded=Exists(excluded_providers)).filter(_job_excluded=False)

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
        _active_assignments_count=Count(
            "job_assignments",
            filter=Q(job_assignments__is_active=True),
            distinct=True,
        )
    )
    qs = qs.filter(_active_assignments_count__lt=MAX_ACTIVE_JOBS)

    qs = qs.annotate(
        _load_penalty=ExpressionWrapper(
            (Cast(F("_active_assignments_count"), FloatField()) / Value(float(MAX_ACTIVE_JOBS)))
            * Value(LOAD_WEIGHT),
            output_field=FloatField(),
        )
    )

    qs = qs.annotate(
        _final_score=F("_score") + F("_cooldown_penalty") + F("_load_penalty")
    )

    qs = qs.order_by("_final_score", "provider_id")
    stable_attempt_number = max(int(attempt_number or 1), 1)

    job_location = getattr(job, "location", None)
    if job_location is None:
        return [
            BroadcastCandidate(
                provider_id=provider.provider_id,
                dynamic_score=None,
                dispatch_score=None,
                distance_km=None,
                area_score=provider._score,
                cooldown_penalty=provider._cooldown_penalty,
                load_penalty=float(provider._load_penalty),
            )
            for provider in qs[:limit]
        ]

    grid_window = grid_window_for_radius(
        job_location.latitude,
        job_location.longitude,
        radius_km=BROADCAST_RADIUS_KM,
    )
    grid_candidates = list(
        qs.filter(
            location__grid_lat__range=(
                grid_window["min_grid_lat"],
                grid_window["max_grid_lat"],
            ),
            location__grid_lng__range=(
                grid_window["min_grid_lng"],
                grid_window["max_grid_lng"],
            ),
        ).select_related("location")
    )
    if grid_candidates:
        candidate_providers = grid_candidates
    else:
        candidate_providers = list(qs.select_related("location"))

    nearby_providers = providers_within_radius(
        job_location,
        candidate_providers,
        radius_km=BROADCAST_RADIUS_KM,
    )

    if nearby_providers:
        providers_for_ranking = nearby_providers
    else:
        providers_for_ranking = []
        for provider in candidate_providers:
            try:
                provider_location = provider.location
                distance_km = haversine_distance_km(
                    job_location.latitude,
                    job_location.longitude,
                    provider_location.latitude,
                    provider_location.longitude,
                )
            except ProviderLocation.DoesNotExist:
                distance_km = 50.0
            providers_for_ranking.append((provider, distance_km))

    ranked_candidates = []
    for provider, distance_km in providers_for_ranking:
        runtime_score = provider_runtime_dispatch_score(
            distance_km=distance_km,
            last_job_assigned_at=provider.last_job_assigned_at,
        )
        random_bonus = dispatch_soft_random_bonus(
            job_id=job.job_id,
            provider_id=provider.provider_id,
            attempt_number=stable_attempt_number,
        )
        dispatch_score = dispatch_score_from_base(
            base_dispatch_score=provider.base_dispatch_score,
            distance_km=distance_km,
            last_job_assigned_at=provider.last_job_assigned_at,
            random_bonus=random_bonus,
        )
        ranked_candidates.append(
            BroadcastCandidate(
                provider_id=provider.provider_id,
                dynamic_score=runtime_score,
                dispatch_score=dispatch_score,
                distance_km=distance_km,
                area_score=provider._score,
                cooldown_penalty=provider._cooldown_penalty,
                load_penalty=float(provider._load_penalty),
            )
        )

    ranked_candidates.sort(
        key=lambda candidate: (
            candidate.cooldown_penalty,
            candidate.area_score,
            candidate.load_penalty,
            -(candidate.dispatch_score or candidate.dynamic_score or 0.0),
            candidate.distance_km if candidate.distance_km is not None else 50.0,
            candidate.provider_id,
        )
    )
    return ranked_candidates[:limit]


def get_broadcast_candidates_for_job(job, limit=10):
    return [
        candidate.provider_id
        for candidate in rank_broadcast_candidates_for_job(job, limit=limit)
    ]


def select_broadcast_wave_candidates(
    ranked_candidates,
    *,
    already_attempted=None,
    batch_size=MARKETPLACE_BATCH_SIZE,
    attempt_number=1,
):
    attempted_provider_ids = set(already_attempted or ())
    available_candidates = [
        candidate
        for candidate in ranked_candidates
        if candidate.provider_id not in attempted_provider_ids
    ]

    if not available_candidates:
        return []

    max_wave_size = max(1, int(batch_size or 1))
    min_wave_size = min(max_wave_size, MIN_DYNAMIC_WAVE_SIZE)
    if len(available_candidates) <= min_wave_size:
        return [candidate.provider_id for candidate in available_candidates]

    top_candidate = available_candidates[0]
    if top_candidate.dispatch_score is None:
        return [
            candidate.provider_id
            for candidate in available_candidates[:max_wave_size]
        ]

    score_gap = min(
        DISPATCH_SCORE_GAP_STEP * max(int(attempt_number or 1), 1),
        DISPATCH_SCORE_GAP_CAP,
    )
    top_score = top_candidate.dispatch_score

    wave = []
    for candidate in available_candidates:
        if len(wave) < min_wave_size:
            wave.append(candidate.provider_id)
            continue
        if len(wave) >= max_wave_size:
            break
        if (top_score - (candidate.dispatch_score or 0.0)) <= score_gap:
            wave.append(candidate.provider_id)
            continue
        break

    return wave


def record_broadcast_attempt(*, job_id: int, provider_id: int, status: str, detail: str | None = None) -> bool:
    """
    Crea un intento por provider/job. Si ya existe, retorna False.
    """
    if JobProviderExclusion.objects.filter(job_id=job_id, provider_id=provider_id).exists():
        return False

    try:
        with transaction.atomic():
            JobBroadcastAttempt.objects.create(
                job_id=job_id,
                provider_id=provider_id,
                status=status,
                detail=detail,
            )
            if status == BroadcastAttemptStatus.SENT:
                from providers.services_metrics import increment_offers_received

                increment_offers_received(provider_id)
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
            transition_job_status(
                job,
                Job.JobStatus.EXPIRED,
                actor=JobEvent.ActorRole.SYSTEM,
                reason="process_on_demand_job:max_attempts",
                allow_legacy=True,
            )
            job.next_alert_at = None
            job.save(update_fields=["next_alert_at", "updated_at"])
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
                log_job_event(
                    job_id=job.job_id,
                    event_type=JobEvent.EventType.TIMEOUT,
                    provider_id=getattr(job, "selected_provider_id", None),
                    note="timeout: pending_client_decision_24h",
                )
                return ("pending_client_decision_timeout_24h", 0, 0)

        if now >= job.marketplace_expires_at:
            transition_job_status(
                job,
                Job.JobStatus.EXPIRED,
                actor=JobEvent.ActorRole.SYSTEM,
                reason="process_marketplace_job:expired_no_provider",
                allow_legacy=True,
            )
            job.next_marketplace_alert_at = None
            job.save(update_fields=["next_marketplace_alert_at", "updated_at"])
            return ("expired_no_provider", 0, 0)

        due = (job.next_marketplace_alert_at is None) or (job.next_marketplace_alert_at <= now)
        if not due:
            return ("not_due", 0, 0)

        if job.marketplace_attempts >= MARKETPLACE_MAX_ATTEMPTS:
            transition_job_status(
                job,
                Job.JobStatus.EXPIRED,
                actor=JobEvent.ActorRole.SYSTEM,
                reason="process_marketplace_job:expired_max_attempts",
                allow_legacy=True,
            )
            job.next_marketplace_alert_at = None
            job.save(update_fields=["next_marketplace_alert_at", "updated_at"])
            return ("expired_max_attempts", 0, 0)

        attempt_number = int(job.marketplace_attempts or 0) + 1
        job.next_marketplace_alert_at = now + timedelta(hours=MARKETPLACE_RETRY_HOURS)

        desired_pool = max(attempt_number * MARKETPLACE_BATCH_SIZE * 3, MARKETPLACE_BATCH_SIZE)
        ranked_candidates = rank_broadcast_candidates_for_job(
            job,
            limit=desired_pool,
            attempt_number=attempt_number,
        )

        already_attempted = set(
            JobBroadcastAttempt.objects.filter(job_id=job.job_id).values_list("provider_id", flat=True)
        )
        wave = select_broadcast_wave_candidates(
            ranked_candidates,
            already_attempted=already_attempted,
            batch_size=MARKETPLACE_BATCH_SIZE,
            attempt_number=attempt_number,
        )

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
        if created_count > 0 and not job.marketplace_search_started_at:
            log_job_event(
                job_id=job.job_id,
                event_type=JobEvent.EventType.POSTED,
                note="job posted",
            )
            create_job_event(
                job=job,
                event_type=JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
                actor_role=JobEvent.ActorRole.SYSTEM,
                payload={"source": "process_marketplace_job", "created_count": created_count},
                job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
                note="marketplace dispatch started waiting provider response",
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
            create_job_event(
                job=job,
                event_type=JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
                actor_role=JobEvent.ActorRole.CLIENT,
                payload={"source": MARKETPLACE_ACTION_EXTEND_SEARCH_24H},
                job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
                note="client extended marketplace search",
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
            create_job_event(
                job=job,
                event_type=JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
                actor_role=JobEvent.ActorRole.CLIENT,
                payload={
                    "source": MARKETPLACE_ACTION_EDIT_SCHEDULE_DATE,
                    "scheduled_date": new_date.isoformat(),
                },
                job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
                note="client updated schedule and resumed search",
            )
            return "schedule_updated"

        if action == MARKETPLACE_ACTION_SWITCH_TO_URGENT:
            if job.job_status != Job.JobStatus.PENDING_CLIENT_DECISION:
                raise MarketplaceDecisionConflict("INVALID_STATUS_FOR_SWITCH_TO_URGENT")

            Job.objects.filter(pk=job.job_id).update(
                job_mode=Job.JobMode.ON_DEMAND,
                scheduled_date=None,
                job_status=Job.JobStatus.POSTED,
                next_alert_at=now,
                is_asap=True,
                next_marketplace_alert_at=None,
                marketplace_search_started_at=None,
                marketplace_expires_at=None,
                marketplace_attempts=0,
            )
            log_job_event(
                job_id=job.job_id,
                event_type=JobEvent.EventType.POSTED,
                note="job posted",
            )
            return "switched_to_urgent"

        if action == MARKETPLACE_ACTION_CANCEL_JOB:
            allowed_statuses = (
                Job.JobStatus.PENDING_CLIENT_DECISION,
                Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            )
            if job.job_status not in allowed_statuses:
                raise MarketplaceDecisionConflict("INVALID_STATUS_FOR_CANCEL")

            selected_provider_id = getattr(job, "selected_provider_id", None)
            provider_id = _resolve_active_provider_id_for_job(job)
            _deactivate_active_assignments_for_job(
                job=job,
                actor_role=JobEvent.ActorRole.CLIENT,
                reason="apply_client_marketplace_decision:cancel",
            )
            cancelled_by = Job.CancellationActor.CLIENT
            Job.objects.filter(pk=job.job_id).update(
                job_status=Job.JobStatus.CANCELLED,
                cancelled_by=cancelled_by,
                cancel_reason=Job.CancelReason.SYSTEM,
                next_marketplace_alert_at=None,
                marketplace_search_started_at=None,
                client_confirmation_started_at=None,
                selected_provider_id=None,
            )
            log_job_event(
                job_id=job.job_id,
                event_type=JobEvent.EventType.CANCELLED,
                provider_id=selected_provider_id,
                note="cancelled",
            )
            create_job_event(
                job=job,
                event_type=JobEvent.EventType.JOB_CANCELLED,
                actor_role=JobEvent.ActorRole.CLIENT,
                provider_id=selected_provider_id,
                payload={"source": MARKETPLACE_ACTION_CANCEL_JOB},
                unique_per_job=True,
                job_status=Job.JobStatus.CANCELLED,
            )
            if provider_id and cancelled_by == Job.CancellationActor.PROVIDER:
                from providers.services_metrics import increment_cancelled

                increment_cancelled(provider_id)
            return "cancelled"

        raise MarketplaceDecisionConflict("INVALID_ACTION")


def accept_marketplace_offer(*, job_id: int, provider_id: int, now=None) -> str:
    now = now or timezone.now()

    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)
        provider = Provider.objects.get(pk=provider_id)
        if not provider.is_operational:
            raise ValidationError(
                "Provider is not operational. Complete onboarding requirements."
            )

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

        job.require_pricing_snapshot()

        Job.objects.filter(pk=job.job_id).update(
            selected_provider_id=provider_id,
            job_status=Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            client_confirmation_started_at=now,
            next_marketplace_alert_at=None,
        )
        log_job_event(
            job_id=job.job_id,
            event_type=JobEvent.EventType.PROVIDER_ACCEPTED,
            provider_id=provider_id,
            note="provider accepted offer",
        )
        create_job_event(
            job=job,
            event_type=JobEvent.EventType.JOB_ACCEPTED,
            actor_role=JobEvent.ActorRole.PROVIDER,
            provider_id=provider_id,
            payload={"source": "accept_marketplace_offer"},
            unique_per_job=True,
            job_status=Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
        )
        JobBroadcastAttempt.objects.filter(pk=attempt.pk).update(
            status=BroadcastAttemptStatus.ACCEPTED,
        )
        from providers.services_metrics import record_offer_accepted

        record_offer_accepted(
            provider_id,
            response_seconds=(now - attempt.created_at).total_seconds(),
        )

    return "accepted_waiting_client"


def accept_provider_offer(*, job_id: int, provider_id: int, now=None) -> str:
    job = Job.objects.only("job_id", "job_mode").get(pk=job_id)

    if job.job_mode == Job.JobMode.SCHEDULED:
        return accept_marketplace_offer(job_id=job_id, provider_id=provider_id, now=now)

    if job.job_mode == Job.JobMode.ON_DEMAND:
        from jobs.services_lifecycle import accept_job_by_provider

        provider = Provider.objects.get(pk=provider_id)
        accept_job_by_provider(job, provider)
        return "accepted_assigned"

    raise ProviderAcceptConflict("INVALID_JOB_MODE_FOR_PROVIDER_ACCEPT")


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

        log_marketplace_timeout(job.job_id, "pending_client_confirmation_timeout")

        search_deadline = None
        if job.marketplace_search_started_at:
            search_deadline = job.marketplace_search_started_at + timedelta(
                hours=MARKETPLACE_SEARCH_TIMEOUT_HOURS
            )
        if search_deadline is not None and now >= search_deadline:
            selected_provider_id = getattr(job, "selected_provider_id", None)
            _deactivate_active_assignments_for_job(
                job=job,
                actor_role=JobEvent.ActorRole.SYSTEM,
                reason="process_marketplace_client_confirmation_timeout:pending_client_decision",
            )
            Job.objects.filter(pk=job.job_id).update(
                job_status=Job.JobStatus.PENDING_CLIENT_DECISION,
                selected_provider_id=None,
                client_confirmation_started_at=None,
                next_marketplace_alert_at=None,
            )
            log_job_event(
                job_id=job.job_id,
                event_type=JobEvent.EventType.TIMEOUT,
                provider_id=selected_provider_id,
                note="timeout: pending_client_decision_24h",
            )
            log_marketplace_timeout(job.job_id, "to_pending_client_decision")
            return ("timeout_to_pending_client_decision", 1)

        selected_provider_id = getattr(job, "selected_provider_id", None)
        _deactivate_active_assignments_for_job(
            job=job,
            actor_role=JobEvent.ActorRole.SYSTEM,
            reason="process_marketplace_client_confirmation_timeout:reopen_waiting",
        )
        Job.objects.filter(pk=job.job_id).update(
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            selected_provider_id=None,
            client_confirmation_started_at=None,
            next_marketplace_alert_at=now,
        )
        log_job_event(
            job_id=job.job_id,
            event_type=JobEvent.EventType.TIMEOUT,
            provider_id=selected_provider_id,
            note="timeout: client_confirm_60m_revert_to_waiting",
        )
        create_job_event(
            job=job,
            event_type=JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
            actor_role=JobEvent.ActorRole.SYSTEM,
            provider_id=selected_provider_id,
            payload={"source": "process_marketplace_client_confirmation_timeout"},
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            note="client confirmation timeout returned job to waiting",
        )
        log_marketplace_timeout(job.job_id, "reopened_waiting_provider_response")
    return ("timeout_reopened_marketplace", 1)


def reject_marketplace_provider(*, job_id: int, now=None) -> str:
    now = now or timezone.now()

    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)

        if job.job_mode != Job.JobMode.SCHEDULED:
            raise MarketplaceDecisionConflict("INVALID_JOB_MODE_FOR_MARKETPLACE_REJECT")

        if job.job_status != Job.JobStatus.PENDING_CLIENT_CONFIRMATION:
            raise MarketplaceDecisionConflict("INVALID_STATUS_FOR_MARKETPLACE_REJECT")

        selected_provider_id = getattr(job, "selected_provider_id", None)
        _deactivate_active_assignments_for_job(
            job=job,
            actor_role=JobEvent.ActorRole.CLIENT,
            reason="reject_marketplace_provider",
        )
        Job.objects.filter(pk=job.job_id).update(
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            selected_provider_id=None,
            client_confirmation_started_at=None,
            next_marketplace_alert_at=now,
        )
        log_job_event(
            job_id=job.job_id,
            event_type=JobEvent.EventType.TIMEOUT,
            provider_id=selected_provider_id,
            note="client_rejected_provider_reopen_marketplace",
        )
        create_job_event(
            job=job,
            event_type=JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
            actor_role=JobEvent.ActorRole.CLIENT,
            provider_id=selected_provider_id,
            payload={"source": "reject_marketplace_provider"},
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            note="client rejected provider and reopened search",
        )

    return "reopened_marketplace"


def confirm_marketplace_provider(*, job_id: int, now=None) -> str:
    now = now or timezone.now()

    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)

        if job.job_status == Job.JobStatus.ASSIGNED:
            active_provider_id = _resolve_active_provider_id_for_job(job)
            if not active_provider_id:
                raise MarketplaceDecisionConflict("ASSIGNED_WITHOUT_ACTIVE_ASSIGNMENT")
            tax_region_code = _build_tax_region_code(job)
            _ensure_job_estimate_tickets_from_snapshot(
                job=job,
                provider_id=active_provider_id,
                tax_region_code=tax_region_code,
            )
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

        selected_provider_id = job.selected_provider_id
        assignment_id = _activate_marketplace_assignment_for_job(
            job_id=job.job_id,
            provider_id=selected_provider_id,
        )
        _ensure_assignment_fee_off(assignment_id=assignment_id)
        tax_region_code = _build_tax_region_code(job)

        Job.objects.filter(pk=job.job_id).update(
            job_status=Job.JobStatus.ASSIGNED,
            next_marketplace_alert_at=None,
            marketplace_search_started_at=None,
            client_confirmation_started_at=None,
        )
        _ensure_job_estimate_tickets_from_snapshot(
            job=job,
            provider_id=selected_provider_id,
            tax_region_code=tax_region_code,
        )
        log_job_event(
            job_id=job.job_id,
            event_type=JobEvent.EventType.CLIENT_CONFIRMED,
            provider_id=selected_provider_id,
            assignment_id=assignment_id,
            note="client confirmed provider",
        )
        log_job_event(
            job_id=job.job_id,
            event_type=JobEvent.EventType.ASSIGNED,
            provider_id=selected_provider_id,
            assignment_id=assignment_id,
            note="assignment activated",
        )
        create_job_event(
            job=job,
            event_type=JobEvent.EventType.JOB_ACCEPTED,
            actor_role=JobEvent.ActorRole.CLIENT,
            provider_id=selected_provider_id,
            assignment_id=assignment_id,
            payload={"source": "confirm_marketplace_provider"},
            unique_per_job=True,
            job_status=Job.JobStatus.ASSIGNED,
        )

    return "confirmed"


def start_service_by_provider(*, job_id: int, provider_id: int) -> str:
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)
        assignment = JobAssignment.objects.filter(
            job=job,
            is_active=True,
        ).first()
        if not assignment:
            raise ValueError("No active assignment for this job.")
        if assignment.provider_id != provider_id:
            raise ValueError("Provider not authorized to start this job.")

        if job.job_status == Job.JobStatus.IN_PROGRESS:
            return "already_in_progress"
        if job.job_status != Job.JobStatus.ASSIGNED:
            raise MarketplaceDecisionConflict("INVALID_STATUS_FOR_SERVICE_START")

        transition_assignment_status(
            assignment,
            "in_progress",
            actor=JobEvent.ActorRole.PROVIDER,
            reason="start_service_by_provider",
        )
        if assignment.accepted_at is None:
            assignment.accepted_at = timezone.now()
        assignment.save(
            update_fields=["accepted_at", "updated_at"]
        )

        transition_job_status(
            job,
            Job.JobStatus.IN_PROGRESS,
            actor=JobEvent.ActorRole.PROVIDER,
            reason="start_service_by_provider",
        )
        create_job_event(
            job=job,
            event_type=JobEvent.EventType.JOB_IN_PROGRESS,
            actor_role=JobEvent.ActorRole.PROVIDER,
            provider_id=provider_id,
            assignment_id=assignment.assignment_id,
            payload={"source": "start_service_by_provider"},
            unique_per_job=True,
        )

    return "started"


def complete_service_by_provider(*, job_id: int, provider_id: int) -> str:
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)

        dispute = getattr(job, "dispute", None)
        if dispute and dispute.status in (
            dispute.DisputeStatus.OPEN,
            dispute.DisputeStatus.UNDER_REVIEW,
        ):
            raise MarketplaceDecisionConflict("DISPUTE_OPEN")

        assignment = JobAssignment.objects.filter(
            job=job,
            is_active=True,
        ).first()
        if not assignment:
            raise ValueError("No active assignment for this job.")

        if assignment.provider_id != provider_id:
            raise ValueError("Provider not authorized to complete this job.")

        if job.job_status == JobStatus.COMPLETED:
            return "already_completed"

        if job.job_status != JobStatus.IN_PROGRESS:
            raise MarketplaceDecisionConflict("INVALID_STATUS_FOR_COMPLETION")

        transition_assignment_status(
            assignment,
            "completed",
            actor=JobEvent.ActorRole.PROVIDER,
            reason="complete_service_by_provider",
        )
        assignment.completed_at = timezone.now()
        assignment.save(
            update_fields=["completed_at", "updated_at"]
        )

        transition_job_status(
            job,
            Job.JobStatus.COMPLETED,
            actor=JobEvent.ActorRole.PROVIDER,
            reason="complete_service_by_provider",
        )

        JobEvent.objects.create(
            job=job,
            event_type=JobEvent.EventType.CLIENT_CONFIRM_REQUESTED,
            provider_id=provider_id,
            assignment_id=assignment.assignment_id,
            note="Provider marked job as completed",
        )
        create_job_event(
            job=job,
            event_type=JobEvent.EventType.JOB_COMPLETED,
            actor_role=JobEvent.ActorRole.PROVIDER,
            provider_id=provider_id,
            assignment_id=assignment.assignment_id,
            payload={"source": "complete_service_by_provider"},
            unique_per_job=True,
        )

    return "completed"


def confirm_service_closed_by_client(
    *, job_id: int, client_id: int, source: str = "manual"
) -> str:
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)

        if job.client_id != client_id:
            raise PermissionError("client_not_allowed")

        provider_id = _resolve_active_provider_id_for_job(job)
        if not provider_id:
            raise MarketplaceDecisionConflict("MISSING_PROVIDER_FOR_JOB")

        dispute = getattr(job, "dispute", None)
        if dispute and dispute.status in (
            dispute.DisputeStatus.OPEN,
            dispute.DisputeStatus.UNDER_REVIEW,
        ):
            raise MarketplaceDecisionConflict("DISPUTE_OPEN")

        run_id = f"AUTO_CLOSE_{timezone.now().strftime('%Y%m%d_%H%M%S')}_job_{job.job_id}"
        tax_region_code = _build_tax_region_code(job)
        provider_subtotal_cents = job_snapshot_subtotal_cents(job)
        currency = job_snapshot_currency(job)

        if job.job_status == Job.JobStatus.CONFIRMED:
            _ensure_job_estimate_tickets_from_snapshot(
                job=job,
                provider_id=provider_id,
                tax_region_code=tax_region_code,
            )
            pt = finalize_provider_ticket(
                provider_id=provider_id,
                ref_type="job",
                ref_id=job.job_id,
                subtotal_cents=provider_subtotal_cents,
                tax_cents=0,
                total_cents=provider_subtotal_cents,
                currency=currency,
                tax_region_code=tax_region_code,
            )
            recalc_provider_ticket_totals(pt.pk)
            if job.client_id:
                (
                    client_subtotal_cents,
                    client_tax_cents,
                    client_total_cents,
                    client_currency,
                    client_tax_region_code,
                ) = _client_ticket_snapshot_for_finalization(
                    job=job,
                    client_id=job.client_id,
                    job_id=job.job_id,
                    fallback_currency=currency,
                    fallback_tax_region_code=tax_region_code,
                )
                ct = finalize_client_ticket(
                    client_id=job.client_id,
                    ref_type="job",
                    ref_id=job.job_id,
                    subtotal_cents=client_subtotal_cents,
                    tax_cents=client_tax_cents,
                    total_cents=client_total_cents,
                    currency=client_currency,
                    tax_region_code=client_tax_region_code,
                )
            finalize_platform_ledger_for_job(job.job_id, run_id=run_id)
            evidence_dir = getattr(settings, "NODO_EVIDENCE_DIR", None)
            try_write_job_evidence_json(
                job.job_id,
                out_dir=evidence_dir,
                run_id=run_id,
                source="finalize",
            )
            return "already_confirmed"

        if job.job_status not in (Job.JobStatus.IN_PROGRESS, Job.JobStatus.COMPLETED):
            raise MarketplaceDecisionConflict("INVALID_STATUS_FOR_FINAL_CLOSE")

        transition_job_status(
            job,
            Job.JobStatus.CONFIRMED,
            actor=JobEvent.ActorRole.CLIENT,
            reason="confirm_service_closed_by_client",
        )
        from providers.services_metrics import increment_completed

        provider_id = _resolve_active_provider_id_for_job(job)
        if provider_id:
            increment_completed(provider_id)
        _ensure_job_estimate_tickets_from_snapshot(
            job=job,
            provider_id=provider_id,
            tax_region_code=tax_region_code,
        )

        pt = finalize_provider_ticket(
            provider_id=provider_id,
            ref_type="job",
            ref_id=job.job_id,
            subtotal_cents=provider_subtotal_cents,
            tax_cents=0,
            total_cents=provider_subtotal_cents,
            currency=currency,
            tax_region_code=tax_region_code,
        )
        recalc_provider_ticket_totals(pt.pk)
        if job.client_id:
            (
                client_subtotal_cents,
                client_tax_cents,
                client_total_cents,
                client_currency,
                client_tax_region_code,
            ) = _client_ticket_snapshot_for_finalization(
                job=job,
                client_id=job.client_id,
                job_id=job.job_id,
                fallback_currency=currency,
                fallback_tax_region_code=tax_region_code,
            )
            ct = finalize_client_ticket(
                client_id=job.client_id,
                ref_type="job",
                ref_id=job.job_id,
                subtotal_cents=client_subtotal_cents,
                tax_cents=client_tax_cents,
                total_cents=client_total_cents,
                currency=client_currency,
                tax_region_code=client_tax_region_code,
            )
        finalize_platform_ledger_for_job(job.job_id, run_id=run_id)
        evidence_dir = getattr(settings, "NODO_EVIDENCE_DIR", None)
        try_write_job_evidence_json(
            job.job_id,
            out_dir=evidence_dir,
            run_id=run_id,
            source="finalize",
        )
        assignment = (
            job.assignments.filter(is_active=True).order_by("-assignment_id").first()
        )
        log_job_event(
            job_id=job.job_id,
            event_type=JobEvent.EventType.CLIENT_CONFIRMED,
            provider_id=provider_id,
            assignment_id=getattr(assignment, "assignment_id", None),
            note="auto_timeout_72h" if source == "auto_timeout" else "",
        )
        create_job_event(
            job=job,
            event_type=JobEvent.EventType.JOB_COMPLETED,
            actor_role=JobEvent.ActorRole.CLIENT,
            provider_id=provider_id,
            assignment_id=getattr(assignment, "assignment_id", None),
            payload={"source": "confirm_service_closed_by_client", "close_source": source},
            unique_per_job=True,
            job_status=Job.JobStatus.CONFIRMED,
        )

    return "closed_and_confirmed"


def resolve_job_dispute_client_wins(
    *, job_id: int, admin_user, resolution_note: str, public_resolution_note: str
) -> str:
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)

        if not hasattr(job, "dispute"):
            raise MarketplaceDecisionConflict("DISPUTE_NOT_FOUND")

        dispute = job.dispute

        if dispute.status not in (
            JobDispute.DisputeStatus.OPEN,
            JobDispute.DisputeStatus.UNDER_REVIEW,
        ):
            raise MarketplaceDecisionConflict("DISPUTE_NOT_ACTIVE")

        if job.job_status == JobStatus.CONFIRMED:
            raise MarketplaceDecisionConflict("JOB_ALREADY_CONFIRMED")

        assignment = (
            JobAssignment.objects.select_for_update()
            .filter(job=job, is_active=True)
            .order_by("-assignment_id")
            .first()
        )
        provider_id = getattr(assignment, "provider_id", None) or _resolve_active_provider_id_for_job(job)

        transition_job_status(
            job,
            JobStatus.CANCELLED,
            actor=JobEvent.ActorRole.ADMIN,
            reason="resolve_job_dispute_client_wins",
        )
        job.cancel_reason = Job.CancelReason.DISPUTE_APPROVED
        job.save(update_fields=["cancel_reason", "updated_at"])

        if assignment:
            transition_assignment_status(
                assignment,
                "cancelled",
                actor=JobEvent.ActorRole.ADMIN,
                reason="resolve_job_dispute_client_wins",
            )

        dispute.status = JobDispute.DisputeStatus.RESOLVED
        dispute.resolved_at = timezone.now()
        dispute.resolved_by = admin_user
        dispute.resolution_note = resolution_note
        dispute.public_resolution_note = public_resolution_note
        dispute.save(
            update_fields=[
                "status",
                "resolved_at",
                "resolved_by",
                "resolution_note",
                "public_resolution_note",
            ]
        )

        if provider_id:
            apply_dispute_loss_penalty(provider_id=provider_id)
            quality_result = enforce_provider_quality_policy(provider_id=provider_id)
        else:
            quality_result = None

        JobEvent.objects.create(
            job=job,
            event_type=JobEvent.EventType.CANCELLED,
            note="dispute_resolved_client_wins",
        )
        create_job_event(
            job=job,
            event_type=JobEvent.EventType.JOB_CANCELLED,
            actor_role=JobEvent.ActorRole.ADMIN,
            provider_id=provider_id,
            payload={"source": "resolve_job_dispute_client_wins"},
            unique_per_job=True,
        )
        transaction.on_commit(lambda: send_dispute_resolution_email(job))
        if quality_result and quality_result.warning_activated:
            transaction.on_commit(
                lambda provider=quality_result.provider: send_quality_warning_email(
                    provider
                )
            )

    return "dispute_resolved_client_wins"
