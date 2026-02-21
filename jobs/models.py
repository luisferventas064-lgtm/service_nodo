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
        STAFF_ACCEPTED = "staff_accepted", "Staff accepted"
        CLIENT_CONFIRMED = "client_confirmed", "Client confirmed"
        STAFF_CANCELLED = "staff_cancelled", "Staff cancelled"
        CLIENT_CANCELLED = "client_cancelled", "Client cancelled"
        SERVICE_STARTED = "service_started", "Service started"
        SERVICE_COMPLETED = "service_completed", "Service completed"
        HOLD_EXPIRED = "hold_expired", "Hold expired"

    class ActorType(models.TextChoices):
        STAFF = "staff", "Staff"
        CLIENT = "client", "Client"
        SYSTEM = "system", "System"
        PROVIDER_ADMIN = "provider_admin", "Provider admin"

    event_id = models.AutoField(primary_key=True)
    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        db_column="job_id",
        related_name="events",
    )
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    actor_type = models.CharField(max_length=20, choices=ActorType.choices)

    worker = models.ForeignKey(
        "workers.Worker",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="worker_id",
        related_name="job_events",
    )
    client = models.ForeignKey(
        "clients.Client",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="client_id",
        related_name="job_events",
    )
    provider = models.ForeignKey(
        "providers.Provider",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="provider_id",
        related_name="job_events",
    )

    job_status_snapshot = models.CharField(max_length=40, blank=True, default="")
    eta_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "jobs_job_event"
        indexes = [
            models.Index(fields=["job", "created_at"]),
            models.Index(fields=["event_type", "created_at"]),
        ]
        ordering = ["created_at"]

    def __str__(self):
        return f"JobEvent {self.event_id} job={self.job_id} {self.event_type}"


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
