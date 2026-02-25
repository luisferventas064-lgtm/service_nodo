from datetime import datetime, time, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from jobs.models import PlatformLedgerEntry
from providers.models import Provider
from settlements.evidence import write_settlement_evidence
from settlements.models import (
    MonthlySettlementClose,
    ProviderSettlement,
    SettlementStatus,
)


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
                is_final=True,
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
            already_exists = ProviderSettlement.objects.filter(
                provider_id=provider_id,
                period_start=start_dt,
                period_end=end_dt,
            ).exists()

            if already_exists:
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
        job__selected_provider=provider,
        is_final=True,
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
        status=SettlementStatus.PENDING,
    )

    # Vincular ledgers incluidos al settlement creado
    ledger_qs.update(settlement=settlement)

    write_settlement_evidence(settlement, "SETTLEMENT_CREATED")
    return settlement


@transaction.atomic
def approve_settlement(settlement):
    if settlement.status != SettlementStatus.PENDING:
        raise ValidationError("Only pending settlements can be approved")

    settlement.status = SettlementStatus.APPROVED
    settlement.approved_at = timezone.now()
    settlement.save(update_fields=["status", "approved_at"])

    write_settlement_evidence(settlement, "SETTLEMENT_APPROVED")
    return settlement


@transaction.atomic
def mark_settlement_paid(settlement):
    if settlement.status != SettlementStatus.APPROVED:
        raise ValidationError("Only approved settlements can be marked as paid")

    settlement.status = SettlementStatus.PAID
    settlement.paid_at = timezone.now()
    settlement.save(update_fields=["status", "paid_at"])

    write_settlement_evidence(settlement, "SETTLEMENT_PAID")
    return settlement


@transaction.atomic
def cancel_settlement(settlement):
    # No se puede cancelar si ya fue aprobado o pagado
    if settlement.status in (
        SettlementStatus.APPROVED,
        SettlementStatus.PAID,
    ):
        raise ValidationError("Approved or paid settlements cannot be cancelled")

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
