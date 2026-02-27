from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from jobs.evidence import try_write_job_evidence_json
from clients.models import ClientTicket
from jobs.models import Job, PlatformLedgerEntry
from providers.models import ProviderTicket


@dataclass(frozen=True)
class LedgerTotals:
    currency: str
    tax_region_code: Optional[str]

    gross_cents: int
    tax_cents: int
    fee_cents: int

    net_provider_cents: int
    platform_revenue_cents: int

    fee_payer: str  # "client" | "provider" | "split"


def _get_provider_ticket(job: Job) -> ProviderTicket | None:
    pt = getattr(job, "provider_ticket", None)
    if pt is not None:
        return pt

    qs = ProviderTicket.objects.filter(ref_type="job", ref_id=job.job_id)
    if job.selected_provider_id:
        qs = qs.filter(provider_id=job.selected_provider_id)
    return qs.order_by("-provider_ticket_id").first()


def _get_client_ticket(job: Job) -> ClientTicket | None:
    ct = getattr(job, "client_ticket", None)
    if ct is not None:
        return ct

    qs = ClientTicket.objects.filter(ref_type="job", ref_id=job.job_id)
    if job.client_id:
        qs = qs.filter(client_id=job.client_id)
    return qs.order_by("-client_ticket_id").first()


def _sum_provider_ticket(job: Job) -> Tuple[int, int]:
    """
    Returns (gross_cents, tax_cents) from provider-side lines.
    Convention: line_total_cents is GROSS (includes tax).
    """
    pt = _get_provider_ticket(job)
    if not pt:
        return 0, 0

    lines = pt.lines.all()
    gross = sum(int(l.line_total_cents or 0) for l in lines)
    tax = sum(int(l.tax_cents or 0) for l in lines)
    return gross, tax


def _sum_client_ticket(job: Job) -> Tuple[int, int]:
    """
    Returns (gross_cents, tax_cents) from client-side lines.
    Convention: line_total_cents is GROSS (includes tax).
    """
    ct = _get_client_ticket(job)
    if not ct:
        return 0, 0

    lines = ct.lines.all()
    gross = sum(int(l.line_total_cents or 0) for l in lines)
    tax = sum(int(l.tax_cents or 0) for l in lines)
    return gross, tax


def _is_fee_line(line) -> bool:
    # Prefer explicit line type when available.
    if getattr(line, "line_type", "") == "fee":
        return True

    # Fallback for legacy/untyped data.
    desc = (getattr(line, "description", "") or "").upper()
    return "FEE" in desc


def _sum_fee_net_from_lines(lines) -> int:
    fee_net = 0
    for l in lines:
        if _is_fee_line(l):
            gross = int(l.line_total_cents or 0)
            tax = int(l.tax_cents or 0)
            fee_net += gross - tax
    return fee_net


def compute_ledger_totals_from_job(job: Job) -> LedgerTotals:
    """
    Minimal v1 logic:
    - gross/tax: prefer client ticket as source of truth if it exists; else provider ticket.
    - fee_cents: sum of fee NET from fee lines (gross - tax).
    - platform_revenue_cents: fee net total.
    - net_provider_cents: provider subtotal net minus provider fee net.
    - fee_payer: inferred from where fee lines exist.
    - region snapshot: pick from any line tax_region_code if present.
    """
    ct = _get_client_ticket(job)
    pt = _get_provider_ticket(job)

    client_lines = list(ct.lines.all()) if ct else []
    provider_lines = list(pt.lines.all()) if pt else []

    client_gross, client_tax = _sum_client_ticket(job)
    provider_gross, provider_tax = _sum_provider_ticket(job)

    if client_gross > 0 or client_tax > 0:
        gross = client_gross
        tax = client_tax
    else:
        gross = provider_gross
        tax = provider_tax

    currency = (ct.currency if ct else None) or (pt.currency if pt else None) or "CAD"

    # region snapshot (first non-null from either side)
    region_code = None
    for l in client_lines:
        if l.tax_region_code:
            region_code = l.tax_region_code
            break
    if region_code is None and ct and ct.tax_region_code:
        region_code = ct.tax_region_code

    if region_code is None:
        for l in provider_lines:
            if l.tax_region_code:
                region_code = l.tax_region_code
                break
    if region_code is None and pt and pt.tax_region_code:
        region_code = pt.tax_region_code

    client_fee_net = _sum_fee_net_from_lines(client_lines)
    provider_fee_net = _sum_fee_net_from_lines(provider_lines)

    if client_fee_net > 0 and provider_fee_net > 0:
        fee_payer = PlatformLedgerEntry.FEE_PAYER_SPLIT
    elif client_fee_net > 0:
        fee_payer = PlatformLedgerEntry.FEE_PAYER_CLIENT
    elif provider_fee_net > 0:
        fee_payer = PlatformLedgerEntry.FEE_PAYER_PROVIDER
    else:
        fee_payer = PlatformLedgerEntry.FEE_PAYER_CLIENT

    fee_cents = client_fee_net + provider_fee_net
    platform_rev = fee_cents

    provider_subtotal = provider_gross - provider_tax
    net_provider = provider_subtotal - provider_fee_net

    return LedgerTotals(
        currency=currency,
        tax_region_code=region_code,
        gross_cents=gross,
        tax_cents=tax,
        fee_cents=fee_cents,
        net_provider_cents=net_provider,
        platform_revenue_cents=platform_rev,
        fee_payer=fee_payer,
    )


@transaction.atomic
def upsert_platform_ledger_entry(job_id: int, *, force: bool = False) -> PlatformLedgerEntry:
    existing_entry = (
        PlatformLedgerEntry.objects.select_for_update()
        .filter(job_id=job_id, is_adjustment=False)
        .first()
    )
    if existing_entry and existing_entry.is_final and not force:
        return existing_entry

    job = Job.objects.select_for_update().get(pk=job_id)
    totals = compute_ledger_totals_from_job(job)

    entry, _created = PlatformLedgerEntry.objects.update_or_create(
        job=job,
        is_adjustment=False,
        defaults=dict(
            currency=totals.currency,
            tax_region_code=totals.tax_region_code,
            gross_cents=totals.gross_cents,
            tax_cents=totals.tax_cents,
            fee_cents=totals.fee_cents,
            net_provider_cents=totals.net_provider_cents,
            platform_revenue_cents=totals.platform_revenue_cents,
            fee_payer=totals.fee_payer,
        ),
    )
    return entry


@transaction.atomic
def finalize_platform_ledger_for_job(job_id: int, run_id: str | None = None) -> PlatformLedgerEntry:
    """
    Called when a job is finalized/confirmed.
    Must be safe to call multiple times (idempotent).
    """
    job = Job.objects.select_for_update().only("job_id", "job_status").get(pk=job_id)

    existing_entry = PlatformLedgerEntry.objects.filter(
        job_id=job_id,
        is_final=True,
        is_adjustment=False,
    ).first()
    if existing_entry:
        return existing_entry

    allowed_final_statuses = {
        Job.JobStatus.COMPLETED,
        Job.JobStatus.CONFIRMED,
    }
    closed_and_confirmed = getattr(Job.JobStatus, "CLOSED_AND_CONFIRMED", None)
    if closed_and_confirmed is not None:
        allowed_final_statuses.add(closed_and_confirmed)

    allowed = {s.value for s in allowed_final_statuses}
    if "closed_and_confirmed" not in allowed:
        allowed.add("closed_and_confirmed")

    if job.job_status not in allowed:
        raise ValidationError(
            f"Cannot finalize ledger: job {job.job_id} status={job.job_status} not in {allowed}"
        )

    # Recompute once right before freeze.
    entry = upsert_platform_ledger_entry(job_id)
    entry = PlatformLedgerEntry.objects.select_for_update().get(pk=entry.pk)

    if entry.is_final:
        return entry

    entry.is_final = True
    entry.finalized_at = timezone.now()
    entry.finalized_run_id = run_id
    entry.finalize_version = 1
    entry.save(
        update_fields=[
            "is_final",
            "finalized_at",
            "finalized_run_id",
            "finalize_version",
            "updated_at",
        ]
    )
    return entry


@transaction.atomic
def rebuild_platform_ledger_for_job(
    job_id: int,
    *,
    run_id: str | None = None,
    reason: str | None = None,
) -> PlatformLedgerEntry:
    job = Job.objects.select_for_update().get(pk=job_id)
    base_entry = (
        PlatformLedgerEntry.objects.select_for_update()
        .filter(job_id=job_id, is_adjustment=False)
        .first()
    )
    allow_protected_rebuild = bool(settings.DEBUG) or bool(
        getattr(settings, "ALLOW_LEDGER_REBUILD", False)
    )
    # FINANCIAL INVARIANT - DO NOT MODIFY:
    # In production mode, finalized or money-linked ledgers cannot be rebuilt.
    if not allow_protected_rebuild:
        if base_entry and base_entry.is_final:
            raise ValidationError("Cannot rebuild finalized ledger in production mode.")

        from payments.models import ClientPayment

        has_registered_payment = ClientPayment.objects.filter(job_id=job_id).exists()
        has_settlement_record = PlatformLedgerEntry.objects.filter(
            job_id=job_id,
            settlement__isnull=False,
        ).exists()
        if has_registered_payment or has_settlement_record:
            raise ValidationError(
                "Cannot rebuild ledger after payment or settlement in production mode."
            )

    allowed_rebuild_statuses = {
        Job.JobStatus.POSTED,
        Job.JobStatus.ASSIGNED,
        Job.JobStatus.IN_PROGRESS,
        Job.JobStatus.COMPLETED,
        Job.JobStatus.CONFIRMED,
    }
    closed_and_confirmed = getattr(Job.JobStatus, "CLOSED_AND_CONFIRMED", None)
    if closed_and_confirmed is not None:
        allowed_rebuild_statuses.add(closed_and_confirmed)

    allowed = {s.value for s in allowed_rebuild_statuses}
    if job.job_status not in allowed:
        raise ValidationError(
            f"Cannot rebuild ledger: job {job.job_id} status={job.job_status} not in {allowed}"
        )

    # Recompute even if the ledger was already finalized.
    # If there is no ledger yet (backfill), this also creates it with is_final=False.
    entry = upsert_platform_ledger_entry(job_id, force=True)
    entry = PlatformLedgerEntry.objects.select_for_update().get(pk=entry.pk)

    entry.rebuild_count = int(entry.rebuild_count or 0) + 1
    entry.last_rebuild_at = timezone.now()
    entry.last_rebuild_run_id = run_id
    entry.last_rebuild_reason = reason[:255] if reason else None
    entry.save(
        update_fields=[
            "rebuild_count",
            "last_rebuild_at",
            "last_rebuild_run_id",
            "last_rebuild_reason",
            "updated_at",
        ]
    )
    evidence_dir = getattr(settings, "NODO_EVIDENCE_DIR", None)
    try_write_job_evidence_json(
        job_id,
        out_dir=evidence_dir,
        run_id=run_id,
        source="rebuild",
    )
    return entry
