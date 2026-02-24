from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from clients.models import Client
from providers.models import Provider
from service_type.models import ServiceType

FIELD_KIND = "job_mode"
FIELD_SCHEDULED = "scheduled_date"

KIND_ON_DEMAND = "on_demand"
KIND_SCHEDULED = "scheduled"


def _get(obj, name: str):
    return getattr(obj, name)


def _set(obj, name: str, value):
    setattr(obj, name, value)


def normalize_job_kind_and_schedule(job) -> None:
    """
    Invariantes:
    1) SCHEDULED requiere scheduled_date futura (para DateField: > hoy).
    2) ON_DEMAND requiere scheduled_date = None.
    3) scheduled_date en pasado/hoy se normaliza a ON_DEMAND y se limpia.
    """
    today = timezone.localdate()
    kind = _get(job, FIELD_KIND)
    sd = _get(job, FIELD_SCHEDULED)

    if sd is not None and sd <= today:
        _set(job, FIELD_KIND, KIND_ON_DEMAND)
        _set(job, FIELD_SCHEDULED, None)
        return

    if kind == KIND_SCHEDULED:
        if sd is None:
            raise ValidationError(
                {FIELD_SCHEDULED: "scheduled_date es requerido cuando job_mode=scheduled."}
            )
        job.is_asap = False
        return

    if kind == KIND_ON_DEMAND:
        if sd is not None:
            _set(job, FIELD_SCHEDULED, None)
        job.is_asap = True
        return

    raise ValidationError({FIELD_KIND: f"Valor invalido para job_mode: {kind!r}"})


class Job(models.Model):
    class JobStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        WAITING_PROVIDER_RESPONSE = "waiting_provider_response", "Waiting Provider Response"
        PENDING_CLIENT_DECISION = "pending_client_decision", "Pending Client Decision"
        HOLD = "hold", "Hold"
        PENDING_PROVIDER_CONFIRMATION = "pending_provider_confirmation", "Pending provider confirmation"
        PENDING_CLIENT_CONFIRMATION = "pending_client_confirmation", "Pending client confirmation"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    hold_provider = models.ForeignKey(
        "providers.Provider",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="held_jobs",
    )
    hold_worker = models.ForeignKey(
        "workers.Worker",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="held_jobs_as_worker",
    )
    hold_expires_at = models.DateTimeField(null=True, blank=True)

    quoted_urgent_total_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    quoted_urgent_fee_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    def is_hold_active(self) -> bool:
        return bool(
            self.hold_provider_id
            and self.hold_expires_at
            and self.hold_expires_at > timezone.now()
        )

    class JobMode(models.TextChoices):
        ON_DEMAND = "on_demand", "On demand"
        SCHEDULED = "scheduled", "Scheduled"

    job_id = models.AutoField(primary_key=True)
    job_mode = models.CharField(
        max_length=20,
        choices=JobMode.choices,
        default=JobMode.SCHEDULED,
        db_index=True,
    )
    job_status = models.CharField(
        max_length=40,
        choices=JobStatus.choices,
        default=JobStatus.DRAFT,
    )

    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        db_column="client_id",
        related_name="jobs",
        null=True,
        blank=True,
    )
    service_type = models.ForeignKey(
        ServiceType,
        on_delete=models.PROTECT,
        db_column="service_type_id",
    )

    country = models.CharField(max_length=100, default="Canada")
    province = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    address_line1 = models.CharField(max_length=255)

    is_asap = models.BooleanField(default=True)
    scheduled_date = models.DateField(blank=True, null=True)
    scheduled_start_time = models.TimeField(blank=True, null=True)
    estimated_duration_min = models.PositiveIntegerField(default=60)

    selected_provider = models.ForeignKey(
        Provider,
        on_delete=models.SET_NULL,
        db_column="selected_provider_id",
        blank=True,
        null=True,
        related_name="selected_jobs",
    )

    quoted_service_skill_id = models.IntegerField(null=True, blank=True)
    quoted_base_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    quoted_currency_code = models.CharField(max_length=3, default="CAD")
    quoted_pricing_unit = models.CharField(max_length=20, default="fixed")
    quoted_emergency_fee_type = models.CharField(max_length=10, default="none")
    quoted_emergency_fee_value = models.DecimalField(
        max_digits=10, decimal_places=2, default="0.00"
    )

    expires_at = models.DateTimeField(blank=True, null=True)
    next_alert_at = models.DateTimeField(null=True, blank=True, db_index=True)
    alert_attempts = models.IntegerField(default=0)
    on_demand_tick_scheduled_at = models.DateTimeField(null=True, blank=True)
    on_demand_tick_dispatched_at = models.DateTimeField(null=True, blank=True)
    tick_attempts = models.PositiveIntegerField(default=0)
    last_tick_attempt_at = models.DateTimeField(null=True, blank=True)
    last_tick_attempt_reason = models.CharField(max_length=64, null=True, blank=True)
    marketplace_attempts = models.IntegerField(default=0)
    marketplace_search_started_at = models.DateTimeField(null=True, blank=True)
    client_confirmation_started_at = models.DateTimeField(null=True, blank=True)
    next_marketplace_alert_at = models.DateTimeField(null=True, blank=True)
    marketplace_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        normalize_job_kind_and_schedule(self)

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.job_mode == self.JobMode.ON_DEMAND:
            self.is_asap = True
        elif self.job_mode == self.JobMode.SCHEDULED:
            self.is_asap = False
        return super().save(*args, **kwargs)

    class Meta:
        db_table = "jobs_job"
        constraints = [
            models.CheckConstraint(
                name="ck_job_scheduled_requires_date",
                condition=(
                    ~models.Q(job_mode=KIND_SCHEDULED)
                    | models.Q(scheduled_date__isnull=False)
                ),
            ),
            models.CheckConstraint(
                name="ck_job_on_demand_requires_null_date",
                condition=(
                    ~models.Q(job_mode=KIND_ON_DEMAND)
                    | models.Q(scheduled_date__isnull=True)
                ),
            ),
        ]

    def __str__(self):
        return f"Job {self.job_id} - {self.job_status} - {self.city}"


class JobMedia(models.Model):
    class UploadedBy(models.TextChoices):
        CLIENT = "client", "Client"
        PROVIDER = "provider", "Provider"

    class MediaType(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"

    class Phase(models.TextChoices):
        PRE_SERVICE = "pre_service", "Pre-service"
        IN_PROGRESS = "in_progress", "In-progress"
        POST_SERVICE = "post_service", "Post-service"
        DISPUTE = "dispute", "Dispute"

    media_id = models.AutoField(primary_key=True)
    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        db_column="job_id",
        related_name="media_items",
    )
    uploaded_by = models.CharField(max_length=10, choices=UploadedBy.choices)
    media_type = models.CharField(max_length=10, choices=MediaType.choices)
    phase = models.CharField(max_length=20, choices=Phase.choices, default=Phase.PRE_SERVICE)
    file = models.FileField(upload_to="job_media/%Y/%m/%d/")
    caption = models.CharField(max_length=255, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "jobs_job_media"

    def __str__(self):
        return f"JobMedia {self.media_id} job={self.job_id} {self.media_type} {self.phase}"


class JobEvent(models.Model):
    class EventType(models.TextChoices):
        POSTED = "posted", "posted"
        SEARCH_STARTED = "search_started", "search_started"
        OFFER_MADE = "offer_made", "offer_made"
        PROVIDER_ACCEPTED = "provider_accepted", "provider_accepted"
        CLIENT_CONFIRM_REQUESTED = "client_confirm_requested", "client_confirm_requested"
        CLIENT_CONFIRMED = "client_confirmed", "client_confirmed"
        ASSIGNED = "assigned", "assigned"
        TIMEOUT = "timeout", "timeout"
        CANCELLED = "cancelled", "cancelled"

    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="events",
        db_index=True,
    )

    event_type = models.CharField(max_length=40, choices=EventType.choices, db_index=True)

    provider_id = models.IntegerField(null=True, blank=True, db_index=True)
    assignment_id = models.IntegerField(null=True, blank=True, db_index=True)

    note = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "job_event"
        indexes = [
            models.Index(fields=["job", "event_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.job_id} {self.event_type} {self.created_at}"


class BroadcastAttemptStatus(models.TextChoices):
    SENT = "sent", "Sent"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"


class JobBroadcastAttempt(models.Model):
    attempt_id = models.BigAutoField(primary_key=True)
    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="broadcast_attempts",
    )
    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.CASCADE,
        related_name="job_broadcast_attempts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=BroadcastAttemptStatus.choices)
    detail = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "jobs_job_broadcast_attempt"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "provider"],
                name="uq_job_broadcast_attempt_one_per_provider_per_job",
            ),
        ]


JobStatus = Job.JobStatus
JobMode = Job.JobMode

from decimal import Decimal
from django.db import models
from django.utils import timezone


class FinancialStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    AUTHORIZED = "authorized", "Authorized"
    CAPTURED = "captured", "Captured"
    VOIDED = "voided", "Voided"
    REFUNDED = "refunded", "Refunded"


class JobFinancial(models.Model):
    job = models.OneToOneField(
        "jobs.Job",  # referencia por string para evitar líos de imports
        on_delete=models.CASCADE,
        related_name="financial",
        db_index=True,
    )

    base_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    adjustment_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    adjustment_reason = models.CharField(max_length=255, blank=True, default="")
    final_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    authorization_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    authorized_at = models.DateTimeField(null=True, blank=True)

    captured_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    captured_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=FinancialStatus.choices,
        default=FinancialStatus.DRAFT,
        db_index=True,
    )

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "jobs_job_financial"

    def recalc_final_amount(self) -> Decimal:
        return (self.base_amount or Decimal("0.00")) + (self.adjustment_amount or Decimal("0.00"))


class PlatformLedgerEntry(models.Model):
    FEE_PAYER_CLIENT = "client"
    FEE_PAYER_PROVIDER = "provider"
    FEE_PAYER_SPLIT = "split"

    FEE_PAYER_CHOICES = [
        (FEE_PAYER_CLIENT, "Client"),
        (FEE_PAYER_PROVIDER, "Provider"),
        (FEE_PAYER_SPLIT, "Split"),
    ]

    job = models.OneToOneField(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="ledger_entry",
    )

    currency = models.CharField(max_length=3, default="CAD")

    # Snapshot final (cents)
    gross_cents = models.PositiveIntegerField(default=0)
    tax_cents = models.PositiveIntegerField(default=0)
    fee_cents = models.PositiveIntegerField(default=0)

    net_provider_cents = models.IntegerField(default=0)
    platform_revenue_cents = models.IntegerField(default=0)

    fee_payer = models.CharField(
        max_length=8,
        choices=FEE_PAYER_CHOICES,
        default=FEE_PAYER_CLIENT,
    )

    tax_region_code = models.CharField(max_length=8, blank=True, null=True)

    is_final = models.BooleanField(default=False)
    finalized_at = models.DateTimeField(blank=True, null=True)
    finalized_run_id = models.CharField(max_length=64, blank=True, null=True)
    finalize_version = models.PositiveSmallIntegerField(default=1)
    rebuild_count = models.PositiveIntegerField(default=0)
    last_rebuild_at = models.DateTimeField(blank=True, null=True)
    last_rebuild_run_id = models.CharField(max_length=64, blank=True, null=True)
    last_rebuild_reason = models.CharField(max_length=255, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["tax_region_code"]),
            models.Index(fields=["fee_payer"]),
        ]

    def __str__(self):
        return f"Ledger(job_id={self.job_id}, gross={self.gross_cents}, tax={self.tax_cents}, fee={self.fee_cents})"


class KpiSnapshot(models.Model):
    """
    Snapshot historico del dashboard/KPIs para monitoreo y futura UI.
    Guardamos JSON como texto para maxima compatibilidad con SQL Server.
    """

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    window_hours = models.IntegerField(default=168, db_index=True)

    # JSON serializado (string) para evitar friccion con tipos JSON en SQL Server
    payload_json = models.TextField()

    class Meta:
        db_table = "kpi_snapshot"
        indexes = [
            models.Index(fields=["created_at", "window_hours"]),
        ]

    def __str__(self):
        return f"kpi_snapshot {self.created_at} window={self.window_hours}h"


class ApiIdempotencyKey(models.Model):
    key = models.CharField(max_length=80, unique=True)
    response_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
