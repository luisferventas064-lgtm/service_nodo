from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from clients.models import ClientTicket

from .models import Job, JobFinancial, PlatformLedgerEntry


@dataclass(slots=True)
class ActivityFinancialData:
    total_charged_cents: Optional[int] = None
    payment_status: str = ""
    gross_cents: Optional[int] = None
    platform_fee_cents: Optional[int] = None
    provider_net_cents: Optional[int] = None


def decimal_to_cents(value) -> int | None:
    if value is None:
        return None
    amount = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(amount * 100)


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


def _build_job_financial_map(job_ids):
    return {
        record.job_id: record
        for record in JobFinancial.objects.filter(job_id__in=job_ids).only(
            "job_id",
            "status",
        )
    }


class ActivityFinancialAdapter:
    def __init__(
        self,
        job: Job,
        role: str,
        *,
        ledger=None,
        client_ticket=None,
        job_financial=None,
    ):
        self.job = job
        self.role = role
        self.ledger = ledger
        self.client_ticket = client_ticket
        self.job_financial = job_financial

    def get_client_total_cents(self):
        ticket = self.get_client_ticket()
        if ticket is not None and ticket.total_cents is not None:
            return int(ticket.total_cents)
        return decimal_to_cents(self.job.requested_total_snapshot)

    def get_client_payment_status(self):
        ticket = self.get_client_ticket()
        if ticket is not None and getattr(ticket, "status", ""):
            return ticket.status

        financial = self.get_job_financial()
        if financial is not None and getattr(financial, "status", ""):
            return financial.status

        return ""

    def get_provider_gross_cents(self):
        ledger = self.get_platform_ledger_entry()
        if ledger is None:
            return None
        return int(ledger.gross_cents or 0)

    def get_provider_fee_cents(self):
        ledger = self.get_platform_ledger_entry()
        if ledger is None:
            return None
        return int(ledger.fee_cents or 0)

    def get_provider_net_cents(self):
        ledger = self.get_platform_ledger_entry()
        if ledger is None:
            return None
        return int(ledger.net_provider_cents or 0)

    def get_client_ticket(self) -> Optional[ClientTicket]:
        if self.client_ticket is not None or hasattr(self, "_client_ticket_loaded"):
            return self.client_ticket

        self.client_ticket = (
            ClientTicket.objects.filter(
                ref_type="job",
                ref_id=self.job.job_id,
                client_id=self.job.client_id,
            )
            .only(
                "client_id",
                "ref_id",
                "status",
                "total_cents",
                "created_at",
                "client_ticket_id",
            )
            .order_by("-created_at", "-client_ticket_id")
            .first()
        )
        self._client_ticket_loaded = True
        return self.client_ticket

    def get_job_financial(self) -> Optional[JobFinancial]:
        if self.job_financial is not None or hasattr(self, "_job_financial_loaded"):
            return self.job_financial

        self.job_financial = JobFinancial.objects.filter(job_id=self.job.job_id).only(
            "job_id",
            "status",
        ).first()
        self._job_financial_loaded = True
        return self.job_financial

    def get_platform_ledger_entry(self) -> Optional[PlatformLedgerEntry]:
        if self.ledger is not None or hasattr(self, "_platform_ledger_entry_loaded"):
            return self.ledger

        self.ledger = (
            PlatformLedgerEntry.objects.filter(
                job_id=self.job.job_id,
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
            .order_by("-is_final", "-created_at", "-id")
            .first()
        )
        self._platform_ledger_entry_loaded = True
        return self.ledger

    def build(self) -> ActivityFinancialData:
        if self.role == "client":
            return ActivityFinancialData(
                total_charged_cents=self.get_client_total_cents(),
                payment_status=self.get_client_payment_status(),
            )

        if self.role == "provider":
            return ActivityFinancialData(
                gross_cents=self.get_provider_gross_cents(),
                platform_fee_cents=self.get_provider_fee_cents(),
                provider_net_cents=self.get_provider_net_cents(),
            )

        if self.role == "worker":
            return ActivityFinancialData()

        raise ValueError(f"Unsupported activity actor type: {self.role!r}")


def build_activity_financial_data_map(jobs, role):
    jobs = list(jobs)
    if not jobs:
        return {}

    job_ids = [job.job_id for job in jobs]

    if role == "worker":
        return {job.job_id: ActivityFinancialData() for job in jobs}

    latest_ledgers = {}
    latest_client_tickets = {}
    job_financials = {}

    if role == "client":
        client_tickets = list(
            ClientTicket.objects.filter(ref_type="job", ref_id__in=job_ids)
            .only(
                "ref_id",
                "client_id",
                "status",
                "total_cents",
                "created_at",
                "client_ticket_id",
            )
            .order_by("ref_id", "-created_at", "-client_ticket_id")
        )
        latest_client_tickets = _build_latest_ticket_map(
            client_tickets,
            owner_id_attr="client_id",
        )
        job_financials = _build_job_financial_map(job_ids)

    if role == "provider":
        latest_ledgers = _build_latest_ledger_map(job_ids)

    financial_data_by_job = {}
    for job in jobs:
        adapter = ActivityFinancialAdapter(
            job,
            role,
            ledger=latest_ledgers.get(job.job_id),
            client_ticket=latest_client_tickets.get((job.job_id, job.client_id)),
            job_financial=job_financials.get(job.job_id),
        )
        financial_data_by_job[job.job_id] = adapter.build()

    return financial_data_by_job
