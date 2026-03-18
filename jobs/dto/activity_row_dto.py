from dataclasses import dataclass

from jobs.activity_financial_adapter import ActivityFinancialData
from jobs.activity_financials import cents_to_decimal, format_money
from jobs.models import Job


STATUS_CLASS_BY_JOB_STATUS = {
    Job.JobStatus.POSTED: "activity-status-posted",
    Job.JobStatus.WAITING_PROVIDER_RESPONSE: "activity-status-waiting",
    Job.JobStatus.PENDING_CLIENT_DECISION: "activity-status-pending",
    Job.JobStatus.HOLD: "activity-status-hold",
    Job.JobStatus.PENDING_PROVIDER_CONFIRMATION: "activity-status-pending",
    Job.JobStatus.PENDING_CLIENT_CONFIRMATION: "activity-status-pending",
    Job.JobStatus.ASSIGNED: "activity-status-assigned",
    Job.JobStatus.IN_PROGRESS: "activity-status-in-progress",
    Job.JobStatus.CONFIRMED: "activity-status-confirmed",
    Job.JobStatus.COMPLETED: "activity-status-completed",
    Job.JobStatus.CANCELLED: "activity-status-cancelled",
    Job.JobStatus.EXPIRED: "activity-status-expired",
    Job.JobStatus.DRAFT: "activity-status-draft",
}

COUNTERPARTY_EMPTY_BY_ACTOR_TYPE = {
    "client": "Searching...",
    "provider": "Client unavailable",
    "worker": "Client unavailable",
}

FINANCIAL_HEADERS_BY_ACTOR_TYPE = {
    "client": ("Total charged",),
    "provider": ("Gross", "Platform fee", "Provider net"),
    "worker": (),
}

PAYMENT_RECORDED_STATUSES = {
    "authorized",
    "captured",
    "finalized",
    "refunded",
}


def _format_client_name(client):
    if client is None:
        return ""
    return f"{client.first_name} {client.last_name}".strip()


def _format_provider_name(provider):
    if provider is None:
        return ""
    company_name = (getattr(provider, "company_name", "") or "").strip()
    if company_name:
        return company_name
    return (
        f"{getattr(provider, 'contact_first_name', '')} "
        f"{getattr(provider, 'contact_last_name', '')}"
    ).strip()


def _format_worker_name(worker):
    if worker is None:
        return ""
    return str(worker).strip()


def _get_active_assignment(job):
    assignments = getattr(job, "activity_active_assignments", None) or ()
    return assignments[0] if assignments else None


def _get_provider_name(job):
    provider_name = _format_provider_name(getattr(job, "selected_provider", None))
    if provider_name:
        return provider_name
    assignment = _get_active_assignment(job)
    return _format_provider_name(getattr(assignment, "provider", None))


def _get_worker_name(job):
    worker_name = _format_worker_name(getattr(job, "hold_worker", None))
    if worker_name:
        return worker_name
    assignment = _get_active_assignment(job)
    return _format_worker_name(getattr(assignment, "worker", None))


def _get_counterparty_display(job, actor_type):
    if actor_type == "client":
        provider_name = _get_provider_name(job)
        if provider_name:
            return provider_name, False
    else:
        client_name = _format_client_name(getattr(job, "client", None))
        if client_name:
            return client_name, False

    return COUNTERPARTY_EMPTY_BY_ACTOR_TYPE[actor_type], True


def _get_provider_service_name(job):
    return (
        (job.provider_service_name_snapshot or "").strip()
        or getattr(getattr(job, "provider_service", None), "custom_name", "")
        or "No service option recorded"
    )


def _get_status_note(job):
    if job.job_status != Job.JobStatus.CANCELLED:
        return ""

    cancelled_by = job.get_cancelled_by_display()
    cancel_reason = job.get_cancel_reason_display()
    if cancelled_by and cancel_reason:
        return f"{cancelled_by} - {cancel_reason}"
    return cancelled_by or cancel_reason or ""


def _get_cancel_reason(job):
    if job.job_status != Job.JobStatus.CANCELLED:
        return ""
    return job.get_cancel_reason_display()


def _format_payment_label(payment_status):
    normalized = (payment_status or "").strip()
    if not normalized:
        return ""
    return normalized.replace("_", " ").title()


@dataclass(slots=True)
class ActivityRowDTO:
    job_id: int
    public_reference: str
    created_at: object
    service_name: str
    service_option_name: str
    city: str
    province: str
    postal_code: str
    is_asap: bool
    scheduled_date: object
    scheduled_start_time: object
    counterparty_display: str
    counterparty_missing: bool
    status: str
    status_label: str
    status_class: str
    status_note: str
    payment_recorded: bool
    payment_label: str
    provider_name: str = ""
    worker_name: str = ""
    total_charged_cents: int | None = None
    payment_status: str = ""
    gross_cents: int | None = None
    provider_net_cents: int | None = None
    platform_fee_cents: int | None = None
    total_amount: object | None = None
    total_amount_display: str = ""
    provider_earnings: object | None = None
    provider_earnings_display: str = ""
    platform_fee: object | None = None
    platform_fee_display: str = ""
    financial_cells: tuple[str, ...] = ()
    cancel_reason: str = ""

    @classmethod
    def get_financial_headers(cls, actor_type):
        return FINANCIAL_HEADERS_BY_ACTOR_TYPE.get(actor_type, ())

    @classmethod
    def get_csv_headers(cls, actor_type):
        if actor_type == "client":
            return [
                "Job ID",
                "Date",
                "Service",
                "Provider",
                "Status",
                "Total charged",
                "Cancelled Reason",
            ]
        if actor_type == "provider":
            return [
                "Job ID",
                "Date",
                "Service",
                "Worker",
                "Status",
                "Gross",
                "Platform fee",
                "Provider net",
                "Cancelled Reason",
            ]
        if actor_type == "worker":
            return [
                "Job ID",
                "Date",
                "Service",
                "Provider",
                "Status",
                "Cancelled Reason",
            ]
        raise ValueError(f"Unsupported activity actor type: {actor_type!r}")

    @staticmethod
    def _visible_financials(actor_type, financial: ActivityFinancialData):
        if actor_type == "client":
            return (
                cents_to_decimal(financial.total_charged_cents),
                None,
                None,
            )
        if actor_type == "provider":
            return (
                cents_to_decimal(financial.gross_cents),
                cents_to_decimal(financial.provider_net_cents),
                cents_to_decimal(financial.platform_fee_cents),
            )
        if actor_type == "worker":
            return None, None, None
        raise ValueError(f"Unsupported activity actor type: {actor_type!r}")

    @classmethod
    def _build_financial_cells(
        cls,
        actor_type,
        *,
        total_amount_display,
        provider_earnings_display,
        platform_fee_display,
    ):
        if actor_type == "client":
            return (total_amount_display,)
        if actor_type == "provider":
            return (
                total_amount_display,
                platform_fee_display,
                provider_earnings_display,
            )
        if actor_type == "worker":
            return ()
        raise ValueError(f"Unsupported activity actor type: {actor_type!r}")

    @classmethod
    def from_job(cls, job, *, actor_type, financial: ActivityFinancialData | None = None):
        counterparty_display, counterparty_missing = _get_counterparty_display(job, actor_type)
        financial = financial or ActivityFinancialData()
        payment_status = financial.payment_status
        payment_label = _format_payment_label(payment_status)
        total_amount, provider_earnings, platform_fee = cls._visible_financials(
            actor_type,
            financial,
        )
        total_amount_display = format_money(total_amount)
        provider_earnings_display = format_money(provider_earnings)
        platform_fee_display = format_money(platform_fee)
        provider_name = _get_provider_name(job)
        worker_name = _get_worker_name(job)
        return cls(
            job_id=job.job_id,
            public_reference=job.public_reference,
            created_at=job.created_at,
            service_name=getattr(getattr(job, "service_type", None), "localized_name", ""),
            service_option_name=_get_provider_service_name(job),
            city=job.city,
            province=job.province,
            postal_code=job.postal_code,
            is_asap=job.is_asap,
            scheduled_date=job.scheduled_date,
            scheduled_start_time=job.scheduled_start_time,
            counterparty_display=counterparty_display,
            counterparty_missing=counterparty_missing,
            status=job.job_status,
            status_label=job.get_job_status_display(),
            status_class=STATUS_CLASS_BY_JOB_STATUS.get(
                job.job_status,
                "activity-status-default",
            ),
            status_note=_get_status_note(job),
            payment_recorded=payment_status in PAYMENT_RECORDED_STATUSES,
            payment_label=payment_label,
            provider_name=provider_name,
            worker_name=worker_name,
            total_charged_cents=financial.total_charged_cents,
            payment_status=payment_status,
            gross_cents=financial.gross_cents,
            provider_net_cents=financial.provider_net_cents,
            platform_fee_cents=financial.platform_fee_cents,
            total_amount=total_amount,
            total_amount_display=total_amount_display,
            provider_earnings=provider_earnings,
            provider_earnings_display=provider_earnings_display,
            platform_fee=platform_fee,
            platform_fee_display=platform_fee_display,
            financial_cells=cls._build_financial_cells(
                actor_type,
                total_amount_display=total_amount_display,
                provider_earnings_display=provider_earnings_display,
                platform_fee_display=platform_fee_display,
            ),
            cancel_reason=_get_cancel_reason(job),
        )

    def to_csv_row(self, actor_type):
        row_prefix = [
            self.job_id,
            self.created_at.isoformat(sep=" ", timespec="minutes"),
            self.service_name,
        ]
        if actor_type == "client":
            return row_prefix + [
                self.provider_name,
                self.status_label,
                self.total_amount_display,
                self.cancel_reason,
            ]
        if actor_type == "provider":
            return row_prefix + [
                self.worker_name,
                self.status_label,
                self.total_amount_display,
                self.platform_fee_display,
                self.provider_earnings_display,
                self.cancel_reason,
            ]
        if actor_type == "worker":
            return row_prefix + [
                self.provider_name,
                self.status_label,
                self.cancel_reason,
            ]
        raise ValueError(f"Unsupported activity actor type: {actor_type!r}")
