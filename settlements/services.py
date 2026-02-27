from datetime import date, datetime, time, timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import BigIntegerField, Case, Count, F, Q, Sum, Value, When
from django.utils import timezone

from jobs.models import Job, PlatformLedgerEntry
from core.stripe_client import get_stripe
from providers.models import Provider
from settlements.evidence import write_settlement_evidence
from settlements.models import (
    LedgerAdjustment,
    JobDispute,
    MonthlySettlementClose,
    ProviderSettlement,
    SettlementPayment,
    SettlementStatus,
)

ACTIVE_DISPUTE_STATUSES = (
    JobDispute.Status.OPEN,
    JobDispute.Status.PROVIDER_RESPONDED,
)


def _model_has_field(model, field_name: str) -> bool:
    return any(f.name == field_name for f in model._meta.get_fields())


def _select_for_update_skip_locked(queryset):
    if connection.features.has_select_for_update_skip_locked:
        return queryset.select_for_update(skip_locked=True)
    return queryset.select_for_update()


def _resolve_job_completed_at(job: Job, *, lock_assignment_rows: bool = False):
    completed_at = getattr(job, "completed_at", None)
    if completed_at:
        return completed_at

    assignment_qs = (
        job.assignments.filter(assignment_status="completed", completed_at__isnull=False)
        .order_by("-completed_at")
        .only("completed_at")
    )
    if lock_assignment_rows:
        assignment_qs = assignment_qs.select_for_update()

    assignment = assignment_qs.first()
    return assignment.completed_at if assignment else None


def _get_locked_settlement_job_ids(settlement: ProviderSettlement) -> list[int]:
    job_ids = list(
        settlement.ledger_entries.order_by("job_id").values_list("job_id", flat=True).distinct()
    )
    if not job_ids:
        return []

    list(
        Job.objects.select_for_update()
        .filter(pk__in=job_ids)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    return job_ids


def _has_active_disputes(job_ids: list[int]) -> bool:
    if not job_ids:
        return False
    return JobDispute.objects.filter(
        job_id__in=job_ids,
        status__in=ACTIVE_DISPUTE_STATUSES,
    ).exists()


def validate_provider_stripe_ready(provider: Provider) -> None:
    if not provider.stripe_account_id:
        raise ValidationError("Provider has no Stripe account.")

    if not provider.stripe_onboarding_completed:
        raise ValidationError("Provider onboarding incomplete.")

    if not provider.stripe_payouts_enabled:
        raise ValidationError("Provider payouts not enabled in Stripe.")


def get_previous_week_range(reference_date: date | None = None) -> tuple[date, date]:
    """
    Returns previous calendar week range (Monday..Sunday) for a given date.
    """
    today = reference_date or timezone.localdate()
    start_of_this_week = today - timedelta(days=today.weekday())
    start_of_previous_week = start_of_this_week - timedelta(days=7)
    end_of_previous_week = start_of_this_week - timedelta(days=1)
    return start_of_previous_week, end_of_previous_week


@transaction.atomic
def generate_weekly_settlements(
    *,
    reference_date: date | None = None,
    currency: str = "CAD",
):
    """
    Generates provider settlements for the previous week (Monday..Sunday).

    Eligibility:
    - ledger is final
    - ledger is not disputed (if this field exists)
    - ledger is not already linked to a settlement
    - ledger finalized_at inside previous week
    """
    period_start_date, period_end_date = get_previous_week_range(reference_date)
    period_start_dt = timezone.make_aware(
        datetime.combine(period_start_date, time.min),
        dt_timezone.utc,
    )
    period_end_dt = timezone.make_aware(
        datetime.combine(period_end_date, time.max),
        dt_timezone.utc,
    )

    ledgers = PlatformLedgerEntry.objects.select_for_update().filter(
        Q(is_final=True) | Q(is_adjustment=True),
        settlement__isnull=True,
        finalized_at__gte=period_start_dt,
        finalized_at__lte=period_end_dt,
        job__selected_provider_id__isnull=False,
    )

    period_adjustments_base = LedgerAdjustment.objects.select_for_update().filter(
        created_at__gte=period_start_dt,
        created_at__lt=period_end_dt,
        ledger_entry__job__selected_provider_id__isnull=False,
        settlement__isnull=True,
    )

    if _model_has_field(PlatformLedgerEntry, "is_disputed"):
        ledgers = ledgers.filter(is_disputed=False)

    base_provider_ids = set(
        ledgers.values_list("job__selected_provider_id", flat=True).distinct()
    )
    adjustment_provider_ids = set(
        period_adjustments_base.values_list(
            "ledger_entry__job__selected_provider_id", flat=True
        ).distinct()
    )
    provider_ids = sorted(base_provider_ids | adjustment_provider_ids)

    settlements_created = []

    for provider_id in provider_ids:
        provider_ledgers = ledgers.filter(job__selected_provider_id=provider_id)
        provider_adjustments = period_adjustments_base.filter(
            ledger_entry__job__selected_provider_id=provider_id
        )
        if not provider_ledgers.exists() and not provider_adjustments.exists():
            continue

        existing = ProviderSettlement.objects.filter(
            provider_id=provider_id,
            period_start=period_start_dt,
            period_end=period_end_dt,
            status__in=[SettlementStatus.DRAFT, SettlementStatus.CLOSED],
        ).first()
        if existing:
            provider_ledgers.update(settlement=existing)
            provider_adjustments.update(settlement=existing)
            continue

        immutable_existing = ProviderSettlement.objects.filter(
            provider_id=provider_id,
            period_start=period_start_dt,
            period_end=period_end_dt,
        ).exclude(status__in=[SettlementStatus.DRAFT, SettlementStatus.CLOSED]).first()
        if immutable_existing:
            raise ValidationError(
                "Cannot attach ledgers to immutable settlement "
                f"(id={immutable_existing.pk}, status={immutable_existing.status})."
            )

        aggregates = provider_ledgers.aggregate(
            total_gross=Sum("gross_cents"),
            total_tax=Sum("tax_cents"),
            total_fee=Sum("fee_cents"),
            total_net_provider=Sum("net_provider_cents"),
            total_platform_revenue=Sum("platform_revenue_cents"),
            total_jobs=Count("id"),
        )
        adjustment_aggregates = provider_adjustments.aggregate(
            provider_delta=Sum(
                Case(
                    When(
                        adjustment_type=LedgerAdjustment.AdjustmentType.PROVIDER_DEDUCTION,
                        then=F("amount_cents"),
                    ),
                    default=Value(0),
                    output_field=BigIntegerField(),
                )
            ),
            fee_delta=Sum(
                Case(
                    When(
                        adjustment_type=LedgerAdjustment.AdjustmentType.PLATFORM_FEE_REVERSAL,
                        then=F("amount_cents"),
                    ),
                    default=Value(0),
                    output_field=BigIntegerField(),
                )
            ),
        )

        base_gross = int(aggregates["total_gross"] or 0)
        base_tax = int(aggregates["total_tax"] or 0)
        base_fee = int(aggregates["total_fee"] or 0)
        base_net_provider = int(aggregates["total_net_provider"] or 0)
        base_platform = int(aggregates["total_platform_revenue"] or 0)

        provider_adjustment_delta = int(adjustment_aggregates["provider_delta"] or 0)
        fee_adjustment_delta = int(adjustment_aggregates["fee_delta"] or 0)

        total_gross_cents = base_gross  # Adjustments do not change gross.
        total_tax_cents = base_tax
        total_fee_cents = base_fee + fee_adjustment_delta
        total_net_provider_cents = base_net_provider + provider_adjustment_delta
        total_platform_revenue_cents = base_platform + fee_adjustment_delta

        scheduled_payout_date = period_end_date + timedelta(days=8)

        settlement_kwargs = {
            "provider_id": provider_id,
            "period_start": period_start_dt,
            "period_end": period_end_dt,
            "currency": currency,
            "total_gross_cents": total_gross_cents,
            "total_tax_cents": total_tax_cents,
            "total_fee_cents": total_fee_cents,
            "total_net_provider_cents": total_net_provider_cents,
            "total_platform_revenue_cents": total_platform_revenue_cents,
            "total_jobs": int(aggregates["total_jobs"] or 0),
            "status": SettlementStatus.DRAFT,
            "scheduled_payout_date": scheduled_payout_date,
        }

        settlement = ProviderSettlement.objects.create(**settlement_kwargs)
        provider_ledgers.update(settlement=settlement)
        provider_adjustments.update(settlement=settlement)
        write_settlement_evidence(settlement, "SETTLEMENT_CREATED")
        settlements_created.append(settlement)

    return settlements_created


def generate_settlements_for_period(from_date, to_date):
    """
    Generate settlements for a financial period using finalized_at as source of truth.
    Idempotent by (provider, period_start, period_end).
    """

    if from_date > to_date:
        raise ValueError("from_date cannot be greater than to_date")

    start_dt = timezone.make_aware(
        datetime.combine(from_date, time.min),
        dt_timezone.utc,
    )
    end_dt = timezone.make_aware(
        datetime.combine(to_date, time.max),
        dt_timezone.utc,
    )

    # Hard lock: prevent generation if month already globally closed
    already_closed = MonthlySettlementClose.objects.filter(
        provider__isnull=True,
        is_global=True,
        period_start=start_dt,
        period_end=end_dt,
    ).exists()
    if not already_closed and end_dt.microsecond:
        already_closed = MonthlySettlementClose.objects.filter(
            provider__isnull=True,
            is_global=True,
            period_start=start_dt,
            period_end=end_dt.replace(microsecond=0),
        ).exists()

    if already_closed:
        raise Exception("Financial period already globally closed.")

    summary = {
        "providers_processed": 0,
        "settlements_created": 0,
        "total_gross_cents": 0,
        "total_net_provider_cents": 0,
        "total_platform_revenue_cents": 0,
    }

    with transaction.atomic():
        ledger_qs = (
            PlatformLedgerEntry.objects.select_for_update()
            .filter(
                Q(is_final=True) | Q(is_adjustment=True),
                finalized_at__gte=start_dt,
                finalized_at__lte=end_dt,
                settlement__isnull=True,
                job__selected_provider_id__isnull=False,
            )
        )

        if not ledger_qs.exists():
            return summary

        provider_ids = (
            ledger_qs.filter(job__selected_provider__isnull=False)
            .values_list("job__selected_provider_id", flat=True)
            .distinct()
        )

        for provider_id in provider_ids:
            existing_settlement = ProviderSettlement.objects.filter(
                provider_id=provider_id,
                period_start=start_dt,
                period_end=end_dt,
            ).first()

            if existing_settlement:
                if existing_settlement.status == SettlementStatus.PAID:
                    raise ValidationError(
                        "Cannot attach ledgers to immutable settlement "
                        f"(id={existing_settlement.pk}, status={existing_settlement.status})."
                    )
                continue

            try:
                provider = Provider.objects.get(pk=provider_id)
            except Provider.DoesNotExist:
                continue

            try:
                settlement = create_provider_settlement_for_period(
                    provider=provider,
                    start=start_dt,
                    end=end_dt,
                )
            except ValidationError:
                continue

            if not settlement:
                continue

            summary["providers_processed"] += 1
            summary["settlements_created"] += 1

            agg = settlement.ledger_entries.aggregate(
                gross=Sum("gross_cents"),
                net_provider=Sum("net_provider_cents"),
                platform_rev=Sum("platform_revenue_cents"),
            )

            summary["total_gross_cents"] += int(agg["gross"] or 0)
            summary["total_net_provider_cents"] += int(agg["net_provider"] or 0)
            summary["total_platform_revenue_cents"] += int(agg["platform_rev"] or 0)

    return summary


@transaction.atomic
def create_provider_settlement_for_period(provider, start, end, currency="CAD"):
    if start >= end:
        raise ValidationError("period_start must be before period_end")

    # PlatformLedgerEntry has no direct provider FK in this project;
    # provider is resolved through job.selected_provider.
    ledger_qs = PlatformLedgerEntry.objects.select_for_update().filter(
        Q(is_final=True) | Q(is_adjustment=True),
        job__selected_provider=provider,
        finalized_at__gte=start,
        finalized_at__lt=end,
        settlement__isnull=True,  # antifraude: no reusar ledgers ya liquidados
    )

    if not ledger_qs.exists():
        raise ValidationError("No eligible finalized ledger entries for this period")

    aggregates = ledger_qs.aggregate(
        total_gross=Sum("gross_cents"),
        total_tax=Sum("tax_cents"),
        total_fee=Sum("fee_cents"),
        total_net_provider=Sum("net_provider_cents"),
        total_platform_revenue=Sum("platform_revenue_cents"),
        total_jobs=Count("id"),
    )

    settlement = ProviderSettlement.objects.create(
        provider=provider,
        period_start=start,
        period_end=end,
        currency=currency,
        total_gross_cents=int(aggregates["total_gross"] or 0),
        total_tax_cents=int(aggregates["total_tax"] or 0),
        total_fee_cents=int(aggregates["total_fee"] or 0),
        total_net_provider_cents=int(aggregates["total_net_provider"] or 0),
        total_platform_revenue_cents=int(aggregates["total_platform_revenue"] or 0),
        total_jobs=int(aggregates["total_jobs"] or 0),
        status=SettlementStatus.DRAFT,
    )

    # Vincular ledgers incluidos al settlement creado
    ledger_qs.update(settlement=settlement)

    write_settlement_evidence(settlement, "SETTLEMENT_CREATED")
    return settlement


@transaction.atomic
def approve_settlement(settlement):
    locked_settlement = (
        ProviderSettlement.objects.select_for_update()
        .select_related("provider")
        .get(pk=settlement.pk)
    )

    if locked_settlement.status != SettlementStatus.DRAFT:
        raise ValidationError("Only draft settlements can be closed")

    validate_provider_stripe_ready(locked_settlement.provider)

    settlement_job_ids = _get_locked_settlement_job_ids(locked_settlement)
    if _has_active_disputes(settlement_job_ids):
        raise ValueError("Cannot close settlement with active disputes")

    locked_settlement.status = SettlementStatus.CLOSED
    locked_settlement.approved_at = timezone.now()
    locked_settlement.save(update_fields=["status", "approved_at"])

    write_settlement_evidence(locked_settlement, "SETTLEMENT_CLOSED")
    return locked_settlement


@transaction.atomic
def mark_settlement_paid(settlement):
    if not hasattr(settlement, "provider") or settlement.provider is None:
        settlement = (
            ProviderSettlement.objects.select_related("provider")
            .get(pk=settlement.pk)
        )

    validate_provider_stripe_ready(settlement.provider)

    if settlement.status != SettlementStatus.CLOSED:
        raise ValidationError("Only closed settlements can be marked as paid")

    settlement.status = SettlementStatus.PAID
    settlement.paid_at = timezone.now()
    settlement.save(update_fields=["status", "paid_at"])

    write_settlement_evidence(settlement, "SETTLEMENT_PAID")
    return settlement


@transaction.atomic
def execute_settlement_payment(settlement, user, reference: str) -> SettlementPayment:
    if not reference or not reference.strip():
        raise ValueError("Payment reference is required")

    locked_settlement = (
        ProviderSettlement.objects.select_for_update()
        .select_related("provider")
        .get(pk=settlement.pk)
    )

    validate_provider_stripe_ready(locked_settlement.provider)

    if SettlementPayment.objects.filter(settlement=locked_settlement).exists():
        raise ValueError("Already paid")

    # "approved" in current model maps to CLOSED.
    if locked_settlement.status != SettlementStatus.CLOSED:
        raise ValueError("Settlement not approved")

    payment = SettlementPayment.objects.create(
        settlement=locked_settlement,
        executed_at=timezone.now(),
        executed_by=user,
        reference=reference.strip(),
        amount_cents=locked_settlement.total_net_provider_cents,
        stripe_environment=settings.STRIPE_MODE,
    )

    mark_settlement_paid(locked_settlement)
    return payment


@transaction.atomic
def execute_stripe_transfer(settlement, user=None) -> SettlementPayment:
    if user is None:
        raise ValueError("User is required to execute Stripe transfer")

    locked_settlement = (
        ProviderSettlement.objects.select_for_update()
        .select_related("provider")
        .get(pk=settlement.pk)
    )

    validate_provider_stripe_ready(locked_settlement.provider)

    if locked_settlement.status != SettlementStatus.CLOSED:
        raise ValidationError("Only closed settlements can be transferred")

    if settings.STRIPE_MODE == "live" and settings.DEBUG:
        raise RuntimeError("LIVE Stripe not allowed in DEBUG mode")

    existing_payment = SettlementPayment.objects.filter(settlement=locked_settlement).first()
    if existing_payment:
        return existing_payment

    stripe = get_stripe()
    idempotency_key = f"settlement_{locked_settlement.pk}"
    transfer = stripe.Transfer.create(
        amount=int(locked_settlement.total_net_provider_cents),
        currency=(locked_settlement.currency or "CAD").lower(),
        destination=locked_settlement.provider.stripe_account_id,
        metadata={
            "settlement_id": str(locked_settlement.pk),
            "provider_id": str(locked_settlement.provider_id),
        },
        idempotency_key=idempotency_key,
    )

    payment = SettlementPayment.objects.create(
        settlement=locked_settlement,
        executed_at=timezone.now(),
        executed_by=user,
        reference=f"stripe_transfer:{transfer.id}",
        amount_cents=locked_settlement.total_net_provider_cents,
        stripe_transfer_id=transfer.id,
        stripe_idempotency_key=idempotency_key,
        stripe_status="processing",
        stripe_environment=settings.STRIPE_MODE,
    )
    return payment


@transaction.atomic
def open_dispute(job, client, reason: str) -> JobDispute:
    """
    Opens a dispute for a completed job within the 72h dispute window.
    If the related settlement is closed and unpaid, moves it back to draft.
    """
    if not reason or not reason.strip():
        raise ValidationError("Dispute reason is required.")

    locked_job = (
        Job.objects.select_for_update()
        .select_related("selected_provider", "client")
        .get(pk=job.pk)
    )

    if locked_job.job_status != Job.JobStatus.COMPLETED:
        raise ValidationError("Job is not completed.")

    completed_at = _resolve_job_completed_at(locked_job, lock_assignment_rows=True)
    if not completed_at:
        raise ValidationError("Job completion timestamp is missing.")

    if timezone.now() > completed_at + timedelta(hours=72):
        raise ValidationError("Dispute window expired (72h).")

    if locked_job.client_id and locked_job.client_id != client.pk:
        raise ValidationError("Client is not allowed to open dispute for this job.")

    if not locked_job.selected_provider_id:
        raise ValidationError("Job has no selected provider.")

    if JobDispute.objects.select_for_update().filter(job=locked_job).exists():
        raise ValidationError("Dispute already exists for this job.")

    dispute = JobDispute.objects.create(
        job=locked_job,
        provider=locked_job.selected_provider,
        client=client,
        client_reason=reason.strip(),
        status=JobDispute.Status.OPEN,
    )

    settlement = (
        ProviderSettlement.objects.select_for_update()
        .filter(
            provider_id=locked_job.selected_provider_id,
            period_start__date__lte=completed_at.date(),
            period_end__date__gte=completed_at.date(),
            status=SettlementStatus.CLOSED,
            paid_at__isnull=True,
        )
        .first()
    )
    if settlement:
        settlement.status = SettlementStatus.DRAFT
        settlement.save(update_fields=["status"])
        write_settlement_evidence(
            settlement,
            "SETTLEMENT_REOPENED_BY_DISPUTE",
            extra={"job_id": locked_job.pk, "dispute_id": dispute.pk},
        )

    return dispute


@transaction.atomic
def provider_respond_dispute(dispute_id, provider_id, response_text: str) -> JobDispute:
    dispute = JobDispute.objects.select_for_update().get(pk=dispute_id)

    if dispute.status != JobDispute.Status.OPEN:
        raise ValueError("Dispute is not open")

    if dispute.provider_id != provider_id:
        raise PermissionError("Provider mismatch")

    if dispute.provider_response:
        raise ValueError("Provider already responded")

    deadline = dispute.opened_at + timedelta(hours=24)
    if timezone.now() > deadline:
        raise ValueError("Response window expired (24h limit)")

    dispute.provider_response = response_text
    dispute.provider_responded_at = timezone.now()
    dispute.status = JobDispute.Status.PROVIDER_RESPONDED
    dispute.save(
        update_fields=[
            "provider_response",
            "provider_responded_at",
            "status",
        ]
    )

    return dispute


@transaction.atomic
def resolve_dispute(dispute, resolution_type) -> JobDispute:
    locked_dispute = (
        JobDispute.objects.select_for_update()
        .select_related("job")
        .get(pk=dispute.pk)
    )

    if locked_dispute.status == JobDispute.Status.RESOLVED:
        raise ValidationError("Dispute already resolved.")

    allowed_resolution_types = {
        JobDispute.ResolutionType.REFUND_100,
        JobDispute.ResolutionType.NO_REFUND,
    }
    if resolution_type not in allowed_resolution_types:
        raise ValidationError("Invalid resolution type.")

    locked_dispute.resolution_type = resolution_type
    locked_dispute.status = JobDispute.Status.RESOLVED
    locked_dispute.resolved_at = timezone.now()
    locked_dispute.save(update_fields=["resolution_type", "status", "resolved_at"])

    if resolution_type == JobDispute.ResolutionType.NO_REFUND:
        return locked_dispute

    ledger_entry = (
        PlatformLedgerEntry.objects.select_for_update()
        .filter(job=locked_dispute.job)
        .first()
    )
    if not ledger_entry:
        raise ValidationError("Ledger entry not found for this job.")

    gross = int(ledger_entry.gross_cents or 0)
    fee = int(ledger_entry.fee_cents or 0)
    provider_net = int(ledger_entry.net_provider_cents or 0)

    LedgerAdjustment.objects.create(
        ledger_entry=ledger_entry,
        dispute=locked_dispute,
        adjustment_type=LedgerAdjustment.AdjustmentType.CLIENT_REFUND,
        amount_cents=gross,
    )
    LedgerAdjustment.objects.create(
        ledger_entry=ledger_entry,
        dispute=locked_dispute,
        adjustment_type=LedgerAdjustment.AdjustmentType.PROVIDER_DEDUCTION,
        amount_cents=-provider_net,
    )
    LedgerAdjustment.objects.create(
        ledger_entry=ledger_entry,
        dispute=locked_dispute,
        adjustment_type=LedgerAdjustment.AdjustmentType.PLATFORM_FEE_REVERSAL,
        amount_cents=-fee,
    )

    return locked_dispute


@transaction.atomic
def auto_resolve_expired_provider_response(
    *,
    reference_time=None,
    dry_run: bool = False,
) -> list[int]:
    now = reference_time or timezone.now()
    cutoff = now - timedelta(hours=24)

    expired_open_disputes = _select_for_update_skip_locked(
        JobDispute.objects.filter(
            status=JobDispute.Status.OPEN,
            opened_at__lt=cutoff,
        ).order_by("id")
    )

    processed: list[int] = []
    for dispute in expired_open_disputes:
        # Defense-in-depth against legacy inconsistent records.
        if dispute.status != JobDispute.Status.OPEN or dispute.provider_response:
            continue

        deadline = dispute.opened_at + timedelta(hours=24)
        if now <= deadline:
            continue

        if dry_run:
            processed.append(dispute.pk)
            continue

        resolved = resolve_dispute(dispute, JobDispute.ResolutionType.REFUND_100)
        processed.append(resolved.pk)

    return processed


@transaction.atomic
def generate_wednesday_payouts(dry_run: bool = False) -> list[int]:
    """
    Executes payouts for closed settlements whose scheduled payout date has arrived.
    Idempotent and concurrency-safe.
    """
    today = timezone.localdate()
    settlements = _select_for_update_skip_locked(
        ProviderSettlement.objects.filter(
            status=SettlementStatus.CLOSED,
            scheduled_payout_date__lte=today,
            paid_at__isnull=True,
        ).order_by("id")
    )

    processed: list[int] = []
    for settlement in settlements:
        validate_provider_stripe_ready(settlement.provider)

        # Defense-in-depth: avoid marking paid if state changed unexpectedly.
        if settlement.status != SettlementStatus.CLOSED or settlement.paid_at is not None:
            continue

        settlement_job_ids = _get_locked_settlement_job_ids(settlement)
        if _has_active_disputes(settlement_job_ids):
            continue

        if dry_run:
            processed.append(settlement.id)
            continue

        mark_settlement_paid(settlement)
        processed.append(settlement.id)

    return processed


@transaction.atomic
def cancel_settlement(settlement):
    # No se puede cancelar si ya fue cerrado o pagado
    if settlement.status in (
        SettlementStatus.CLOSED,
        SettlementStatus.PAID,
    ):
        raise ValidationError("Closed or paid settlements cannot be cancelled")

    # No se puede cancelar si tiene ledger asociados
    if settlement.ledger_entries.exists():
        raise ValidationError(
            "Settlement with linked ledger entries cannot be cancelled"
        )

    settlement.status = SettlementStatus.CANCELLED
    settlement.save(update_fields=["status"])

    write_settlement_evidence(settlement, "SETTLEMENT_CANCELLED")
    return settlement


def get_provider_monthly_dashboard(provider_id):
    """
    Returns closed monthly financial snapshots for a provider.
    Only returns provider-level closes (not global).
    """
    closes = (
        MonthlySettlementClose.objects.filter(
            provider_id=provider_id,
            is_global=False,
        )
        .order_by("-period_start")
        .values(
            "period_start",
            "period_end",
            "total_gross_cents",
            "total_provider_cents",
            "total_platform_revenue_cents",
            "closed_at",
            "notes",
        )
    )
    return list(closes)


def get_provider_year_summary(provider_id, year=None):
    """
    Returns aggregated totals for a provider for a given year
    using provider-level monthly closes.
    """
    if year is None:
        year = timezone.now().year

    qs = MonthlySettlementClose.objects.filter(
        provider_id=provider_id,
        is_global=False,
        period_start__year=year,
    )

    agg = qs.aggregate(
        total_gross=Sum("total_gross_cents"),
        total_provider=Sum("total_provider_cents"),
        total_platform=Sum("total_platform_revenue_cents"),
    )

    return {
        "year": year,
        "total_gross_cents": agg["total_gross"] or 0,
        "total_provider_cents": agg["total_provider"] or 0,
        "total_platform_revenue_cents": agg["total_platform"] or 0,
    }
