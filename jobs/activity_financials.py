from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from jobs.activity_financial_adapter import ActivityFinancialData
from clients.models import ClientTicket
from providers.models import ProviderTicket

from .activity_financial_adapter import build_activity_financial_data_map
from .models import Job, PlatformLedgerEntry


MONEY_QUANTIZER = Decimal("0.01")
ZERO_MONEY = Decimal("0.00")


def cents_to_decimal(value):
    if value is None:
        return None
    return (Decimal(value) / Decimal("100")).quantize(MONEY_QUANTIZER)


def normalize_money(value):
    if value is None:
        return None
    return Decimal(value).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def format_money(value):
    if value is None:
        return ""
    return f"{normalize_money(value):.2f}"


def is_fee_line(line):
    if getattr(line, "line_type", "") == "fee":
        return True

    description = (getattr(line, "description", "") or "").upper()
    return "FEE" in description


def sum_fee_net_from_lines(lines):
    fee_net = 0
    for line in lines:
        if is_fee_line(line):
            gross = int(line.line_total_cents or 0)
            tax = int(line.tax_cents or 0)
            fee_net += gross - tax
    return fee_net


def _build_latest_ticket_map(tickets, *, owner_id_attr):
    latest_tickets = {}
    for ticket in tickets:
        key = (ticket.ref_id, getattr(ticket, owner_id_attr, None))
        latest_tickets.setdefault(key, ticket)
    return latest_tickets


def _build_latest_ledger_map(job_ids):
    latest_ledgers = {}
    entries = (
        PlatformLedgerEntry.objects.filter(
            job_id__in=job_ids,
            is_adjustment=False,
        )
        .only(
            "job_id",
            "gross_cents",
            "fee_cents",
            "net_provider_cents",
            "is_final",
            "created_at",
        )
        .order_by("-created_at", "-id")
    )

    for entry in entries:
        current = latest_ledgers.get(entry.job_id)
        if current is None or (entry.is_final and not current.is_final):
            latest_ledgers[entry.job_id] = entry

    return latest_ledgers


def build_financial_snapshot_map(jobs):
    jobs = list(jobs)
    if not jobs:
        return {}

    job_ids = [job.job_id for job in jobs]
    latest_ledgers = _build_latest_ledger_map(job_ids)

    client_tickets = list(
        ClientTicket.objects.filter(ref_type="job", ref_id__in=job_ids)
        .prefetch_related("lines")
        .order_by("ref_id", "-created_at", "-client_ticket_id")
    )
    provider_tickets = list(
        ProviderTicket.objects.filter(ref_type="job", ref_id__in=job_ids)
        .prefetch_related("lines")
        .order_by("ref_id", "-created_at", "-provider_ticket_id")
    )

    latest_client_tickets = _build_latest_ticket_map(
        client_tickets,
        owner_id_attr="client_id",
    )
    latest_provider_tickets = _build_latest_ticket_map(
        provider_tickets,
        owner_id_attr="provider_id",
    )

    financials_by_job = {}
    for job in jobs:
        client_ticket = latest_client_tickets.get((job.job_id, job.client_id))
        provider_ticket = latest_provider_tickets.get((job.job_id, job.selected_provider_id))
        latest_ledger = latest_ledgers.get(job.job_id)

        client_lines = list(client_ticket.lines.all()) if client_ticket is not None else []
        provider_lines = list(provider_ticket.lines.all()) if provider_ticket is not None else []

        client_fee_net = sum_fee_net_from_lines(client_lines)
        provider_fee_net = sum_fee_net_from_lines(provider_lines)
        provider_subtotal_cents = sum(
            int(line.line_total_cents or 0) - int(line.tax_cents or 0)
            for line in provider_lines
        )

        if client_ticket is not None:
            total_amount = cents_to_decimal(client_ticket.total_cents)
        elif job.requested_total_snapshot is not None:
            total_amount = normalize_money(job.requested_total_snapshot)
        elif job.quoted_total_price_cents is not None:
            total_amount = cents_to_decimal(job.quoted_total_price_cents)
        elif latest_ledger is not None:
            total_amount = cents_to_decimal(latest_ledger.gross_cents)
        else:
            total_amount = None

        if latest_ledger is not None:
            provider_earnings = cents_to_decimal(latest_ledger.net_provider_cents)
            platform_fee = cents_to_decimal(latest_ledger.fee_cents)
        else:
            provider_earnings = (
                cents_to_decimal(provider_subtotal_cents - provider_fee_net)
                if provider_ticket is not None
                else None
            )
            platform_fee = (
                cents_to_decimal(client_fee_net + provider_fee_net)
                if client_ticket is not None or provider_ticket is not None
                else None
            )

        financials_by_job[job.job_id] = {
            "total_amount": total_amount,
            "provider_earnings": provider_earnings,
            "platform_fee": platform_fee,
        }

    return financials_by_job


def _build_activity_financial_cards(
    actor_type,
    *,
    total_gross,
    total_provider_earnings,
    total_platform_fees,
):
    if actor_type == "client":
        return [
            {
                "label": "Total charged",
                "value": format_money(total_gross),
            }
        ]
    if actor_type == "provider":
        return [
            {
                "label": "Total gross",
                "value": format_money(total_gross),
            },
            {
                "label": "Provider net",
                "value": format_money(total_provider_earnings),
            },
            {
                "label": "Platform fees",
                "value": format_money(total_platform_fees),
            },
        ]
    if actor_type == "worker":
        return []
    raise ValueError(f"Unsupported activity actor type: {actor_type!r}")


def build_activity_analytics(actor_type, jobs, *, financials_by_job=None):
    jobs = list(jobs)
    if financials_by_job is None:
        financials_by_job = build_activity_financial_data_map(jobs, actor_type)

    total_jobs = len(jobs)
    completed_jobs = 0
    cancelled_jobs = 0
    total_gross = ZERO_MONEY
    total_provider_earnings = ZERO_MONEY
    total_platform_fees = ZERO_MONEY

    completed_statuses = {
        Job.JobStatus.COMPLETED,
        Job.JobStatus.CONFIRMED,
    }

    for job in jobs:
        if job.job_status in completed_statuses:
            completed_jobs += 1
        if job.job_status == Job.JobStatus.CANCELLED:
            cancelled_jobs += 1

        financials = financials_by_job.get(job.job_id, ActivityFinancialData())
        if actor_type == "client":
            total_gross += cents_to_decimal(financials.total_charged_cents) or ZERO_MONEY
        elif actor_type == "provider":
            total_gross += cents_to_decimal(financials.gross_cents) or ZERO_MONEY
            total_provider_earnings += (
                cents_to_decimal(financials.provider_net_cents) or ZERO_MONEY
            )
            total_platform_fees += (
                cents_to_decimal(financials.platform_fee_cents) or ZERO_MONEY
            )

    total_gross = normalize_money(total_gross) or ZERO_MONEY
    total_provider_earnings = normalize_money(total_provider_earnings) or ZERO_MONEY
    total_platform_fees = normalize_money(total_platform_fees) or ZERO_MONEY
    financial_cards = _build_activity_financial_cards(
        actor_type,
        total_gross=total_gross,
        total_provider_earnings=total_provider_earnings,
        total_platform_fees=total_platform_fees,
    )

    return {
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "cancelled_jobs": cancelled_jobs,
        "total_gross": total_gross,
        "total_provider_earnings": total_provider_earnings,
        "total_platform_fees": total_platform_fees,
        "total_charged": total_gross,
        "total_gross_display": format_money(total_gross),
        "total_provider_earnings_display": format_money(total_provider_earnings),
        "total_platform_fees_display": format_money(total_platform_fees),
        "total_charged_display": format_money(total_gross),
        "financial_cards": financial_cards,
    }


def _get_job_month(job):
    local_created_at = job.to_local_time(job.created_at) if job.created_at else None
    if local_created_at is None:
        return None
    return local_created_at.date().replace(day=1)


def build_monthly_revenue(jobs):
    jobs = list(jobs)
    financials_by_job = build_financial_snapshot_map(jobs)
    monthly_totals = defaultdict(
        lambda: {
            "gross": ZERO_MONEY,
            "provider": ZERO_MONEY,
            "fees": ZERO_MONEY,
        }
    )

    for job in jobs:
        month = _get_job_month(job)
        if month is None:
            continue

        financials = financials_by_job.get(job.job_id, {})
        month_row = monthly_totals[month]
        month_row["gross"] += financials.get("total_amount") or ZERO_MONEY
        month_row["provider"] += financials.get("provider_earnings") or ZERO_MONEY
        month_row["fees"] += financials.get("platform_fee") or ZERO_MONEY

    rows = []
    for month in sorted(monthly_totals):
        month_row = monthly_totals[month]
        gross = normalize_money(month_row["gross"]) or ZERO_MONEY
        provider = normalize_money(month_row["provider"]) or ZERO_MONEY
        fees = normalize_money(month_row["fees"]) or ZERO_MONEY
        rows.append(
            {
                "month": month,
                "gross": gross,
                "provider": provider,
                "fees": fees,
                "gross_display": format_money(gross),
                "provider_display": format_money(provider),
                "fees_display": format_money(fees),
            }
        )

    return rows
