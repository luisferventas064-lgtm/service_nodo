from __future__ import annotations

import zoneinfo
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from clients.models import Client
from providers.models import Provider
from service_type.models import ServiceType

FIELD_KIND = "job_mode"
FIELD_SCHEDULED = "scheduled_date"

KIND_ON_DEMAND = "on_demand"
KIND_SCHEDULED = "scheduled"

PROVINCE_TIMEZONE_MAP = {
    "QC": "America/Toronto",
    "ON": "America/Toronto",
    "AB": "America/Edmonton",
    "BC": "America/Vancouver",
}


def _get(obj, name: str):
    return getattr(obj, name)


def _set(obj, name: str, value):
    setattr(obj, name, value)


def _job_localdate(job):
    province_code = (getattr(job, "province", None) or "").strip().upper()
    tz_name = PROVINCE_TIMEZONE_MAP.get(province_code, timezone.get_current_timezone_name())
    return timezone.now().astimezone(zoneinfo.ZoneInfo(tz_name)).date()


def normalize_job_kind_and_schedule(job) -> None:
    """
    Invariantes:
    1) SCHEDULED requiere scheduled_date presente o futura.
    2) ON_DEMAND requiere scheduled_date = None.
    3) scheduled_date en pasado se normaliza a ON_DEMAND y se limpia.
    """
    today = _job_localdate(job)
    kind = _get(job, FIELD_KIND)
    sd = _get(job, FIELD_SCHEDULED)

    if sd is not None and sd < today:
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

    raise ValidationError({FIELD_KIND: f"Invalid value for job_mode: {kind!r}"})


class Job(models.Model):
    SNAPSHOT_REQUIRED_ON_STATUS_TRANSITIONS = frozenset(
        {
            "assigned",
            "in_progress",
            "completed",
            "confirmed",
        }
    )
    SNAPSHOT_LOCKED_FIELDS = (
        "quoted_base_price_cents",
        "quoted_currency",
        "quoted_pricing_source",
        "quoted_provider_service_id",
        "quoted_tax_rate_bps",
        "quoted_total_price_cents",
    )

    class JobStatus(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Posted")
        SCHEDULED_PENDING_ACTIVATION = (
            "scheduled_pending_activation",
            _("Scheduled Pending Activation"),
        )
        WAITING_PROVIDER_RESPONSE = "waiting_provider_response", _("Waiting Provider Response")
        PENDING_CLIENT_DECISION = "pending_client_decision", _("Pending Client Decision")
        HOLD = "hold", _("Hold")
        PENDING_PROVIDER_CONFIRMATION = "pending_provider_confirmation", _("Pending provider confirmation")
        PENDING_CLIENT_CONFIRMATION = "pending_client_confirmation", _("Pending client confirmation")
        ASSIGNED = "assigned", _("Assigned")
        IN_PROGRESS = "in_progress", _("In progress")
        COMPLETED = "completed", _("Completed")
        CONFIRMED = "confirmed", _("Confirmed")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")

    class CancellationActor(models.TextChoices):
        CLIENT = "client", _("Client")
        PROVIDER = "provider", _("Provider")
        SYSTEM = "system", _("System")

    class CancelReason(models.TextChoices):
        DISPUTE_APPROVED = "dispute_approved", _("Dispute approved")
        PROVIDER_REJECTED = "provider_rejected", _("Provider rejected")
        CLIENT_CANCELLED = "client_cancelled", _("Client cancelled")
        AUTO_TIMEOUT = "auto_timeout", _("Auto timeout")
        SYSTEM = "system", _("System action")

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
        ON_DEMAND = "on_demand", _("On demand")
        SCHEDULED = "scheduled", _("Scheduled")

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
    cancelled_by = models.CharField(
        max_length=20,
        choices=CancellationActor.choices,
        null=True,
        blank=True,
    )
    cancel_reason = models.CharField(
        max_length=40,
        choices=CancelReason.choices,
        null=True,
        blank=True,
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
    address_line2 = models.CharField(max_length=255, blank=True, default="")
    access_notes = models.TextField(blank=True, default="")

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
    provider_service = models.ForeignKey(
        "providers.ProviderService",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="jobs",
    )
    provider_service_name_snapshot = models.CharField(max_length=255, blank=True, default="")
    requested_subservice_name = models.CharField(max_length=150, blank=True, default="")
    requested_subservice_id_snapshot = models.IntegerField(null=True, blank=True)
    requested_subservice_base_price_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    requested_subtotal_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    requested_tax_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    requested_tax_rate_bps_snapshot = models.IntegerField(
        null=True,
        blank=True,
    )
    requested_tax_region_code_snapshot = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )
    requested_total_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    requested_quantity_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    requested_unit_price_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    requested_billing_unit_snapshot = models.CharField(
        max_length=50,
        blank=True,
        default="",
    )
    requested_base_line_total_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
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
    quoted_base_price_cents = models.BigIntegerField(null=True, blank=True)
    quoted_currency = models.CharField(max_length=3, blank=True, default="")
    quoted_pricing_source = models.CharField(max_length=32, blank=True, default="")
    quoted_provider_service_id = models.BigIntegerField(
        null=True,
        blank=True,
        db_index=True,
    )
    quoted_tax_rate_bps = models.PositiveIntegerField(null=True, blank=True)
    quoted_total_price_cents = models.BigIntegerField(null=True, blank=True)

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

    @staticmethod
    def _decimal_to_cents(value) -> int | None:
        if value is None:
            return None
        amount = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return int(amount * 100)

    def snapshot_base_price_cents(self) -> int | None:
        if self.quoted_base_price_cents is not None:
            return int(self.quoted_base_price_cents)
        return self._decimal_to_cents(self.quoted_base_price)

    def snapshot_total_price_cents(self) -> int | None:
        if self.quoted_total_price_cents is not None:
            return int(self.quoted_total_price_cents)
        return self.snapshot_base_price_cents()

    def snapshot_currency_code(self) -> str:
        value = (self.quoted_currency or self.quoted_currency_code or "").strip().upper()
        return value

    def has_pricing_snapshot(self) -> bool:
        return bool(
            self.snapshot_base_price_cents() is not None
            and self.snapshot_currency_code()
            and (self.quoted_pricing_source or "").strip()
        )

    def require_pricing_snapshot(self) -> None:
        if self.has_pricing_snapshot():
            return
        raise ValidationError(
            {
                "quoted_base_price_cents": (
                    "Job must have a pricing snapshot before entering the active lifecycle."
                )
            }
        )

    def _validate_pricing_snapshot_contract(self) -> None:
        if self._state.adding or not self.pk:
            return

        previous = type(self).objects.filter(pk=self.pk).first()
        if previous is None:
            return

        if previous.has_pricing_snapshot():
            changed_snapshot_fields = [
                field
                for field in self.SNAPSHOT_LOCKED_FIELDS
                if getattr(previous, field) != getattr(self, field)
            ]
            if changed_snapshot_fields:
                raise ValidationError(
                    "Pricing snapshot is immutable once captured."
                )

        status_is_advancing = previous.job_status != self.job_status
        if (
            status_is_advancing
            and self.job_status in self.SNAPSHOT_REQUIRED_ON_STATUS_TRANSITIONS
            and (previous.has_pricing_snapshot() or self.has_pricing_snapshot())
        ):
            self.require_pricing_snapshot()

    def clean(self):
        super().clean()
        normalize_job_kind_and_schedule(self)
        self._validate_pricing_snapshot_contract()

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.job_mode == self.JobMode.ON_DEMAND:
            self.is_asap = True
        elif self.job_mode == self.JobMode.SCHEDULED:
            self.is_asap = False
        return super().save(*args, **kwargs)

    def get_job_timezone(self):
        tz_name = "America/Toronto"
        zone = getattr(self, "zone", None)
        if zone and getattr(zone, "province", None):
            province_code = zone.province
        else:
            province_code = self.province

        province_code = (province_code or "").strip().upper()
        if province_code:
            tz_name = PROVINCE_TIMEZONE_MAP.get(province_code, tz_name)

        return zoneinfo.ZoneInfo(tz_name)

    def to_local_time(self, dt):
        if not dt:
            return None

        return dt.astimezone(self.get_job_timezone())

    def _get_active_assignment(self):
        if hasattr(self, "active_assignment"):
            return self.active_assignment

        return (
            self.assignments.filter(is_active=True).order_by("-assignment_id").first()
        )

    def _get_confirmed_event(self):
        if hasattr(self, "confirmed_event"):
            return self.confirmed_event

        return (
            self.events.filter(event_type=JobEvent.EventType.CLIENT_CONFIRMED)
            .order_by("-created_at")
            .first()
        )

    @property
    def local_started_at(self):
        assignment = self._get_active_assignment()
        if assignment and assignment.accepted_at:
            return self.to_local_time(assignment.accepted_at)
        return None

    @property
    def local_completed_at(self):
        assignment = self._get_active_assignment()
        if assignment and assignment.completed_at:
            return self.to_local_time(assignment.completed_at)
        return None

    @property
    def local_confirmed_at(self):
        event = self._get_confirmed_event()
        if event:
            return self.to_local_time(event.created_at)
        return None

    @property
    def public_reference(self) -> str:
        local_created_at = self.to_local_time(self.created_at)
        year = local_created_at.year if local_created_at else timezone.now().year
        return f"NODO-{year}-{str(self.job_id).zfill(7)}"

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
            models.CheckConstraint(
                name="cancel_reason_required_if_cancelled",
                condition=(
                    Q(job_status="cancelled", cancel_reason__isnull=False)
                    | ~Q(job_status="cancelled")
                ),
            ),
        ]

    def __str__(self):
        return f"Job {self.job_id} - {self.job_status} - {self.city}"


class JobRequestedExtra(models.Model):
    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="requested_extras",
    )
    provider_service_extra = models.ForeignKey(
        "providers.ProviderServiceExtra",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_job_extras",
    )
    extra_name_snapshot = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1)
    unit_price_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    line_total_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "job_requested_extra"
        ordering = ("created_at", "id")

    def __str__(self):
        return f"{self.job_id} - {self.extra_name_snapshot} x {self.quantity}"


class JobDispute(models.Model):
    class DisputeStatus(models.TextChoices):
        OPEN = "open", "Open"
        UNDER_REVIEW = "under_review", "Under Review"
        RESOLVED = "resolved", "Resolved"
        REJECTED = "rejected", "Rejected"

    job = models.OneToOneField(
        Job,
        on_delete=models.CASCADE,
        related_name="dispute",
        db_index=True,
    )
    client_id = models.IntegerField(db_index=True)
    provider_id = models.IntegerField(db_index=True)
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=DisputeStatus.choices,
        default=DisputeStatus.OPEN,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_disputes",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True, default="")
    public_resolution_note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "job_dispute"


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
        JOB_CREATED = "job_created", "job_created"
        SCHEDULED_ACTIVATED = "scheduled_activated", "scheduled_activated"
        WAITING_PROVIDER_RESPONSE = "waiting_provider_response", "waiting_provider_response"
        JOB_ACCEPTED = "job_accepted", "job_accepted"
        PROVIDER_DECLINED = "provider_declined", "provider_declined"
        JOB_IN_PROGRESS = "job_in_progress", "job_in_progress"
        JOB_COMPLETED = "job_completed", "job_completed"
        JOB_EXPIRED = "job_expired", "job_expired"
        JOB_CANCELLED = "job_cancelled", "job_cancelled"

    class ActorRole(models.TextChoices):
        SYSTEM = "system", "System"
        CLIENT = "client", "Client"
        PROVIDER = "provider", "Provider"
        WORKER = "worker", "Worker"
        ADMIN = "admin", "Admin"

    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="events",
        db_index=True,
    )

    event_type = models.CharField(max_length=40, choices=EventType.choices, db_index=True)

    provider_id = models.IntegerField(null=True, blank=True, db_index=True)
    assignment_id = models.IntegerField(null=True, blank=True, db_index=True)
    visible_status = models.CharField(max_length=64, blank=True, default="")
    actor_role = models.CharField(
        max_length=20,
        choices=ActorRole.choices,
        blank=True,
        default=ActorRole.SYSTEM,
    )
    payload_json = models.JSONField(default=dict, blank=True)

    note = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "job_event"
        indexes = [
            models.Index(fields=["job", "event_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.job_id} {self.event_type} {self.created_at}"


class JobProviderExclusion(models.Model):
    class Reason(models.TextChoices):
        DECLINED = "declined", "Declined"

    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="provider_exclusions",
    )
    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.CASCADE,
        related_name="job_exclusions",
    )
    reason = models.CharField(
        max_length=20,
        choices=Reason.choices,
        default=Reason.DECLINED,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "jobs_job_provider_exclusion"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "provider"],
                name="uq_job_provider_exclusion_one_per_provider_per_job",
            ),
        ]

    def __str__(self):
        return f"job={self.job_id} provider={self.provider_id} reason={self.reason}"


class BroadcastAttemptStatus(models.TextChoices):
    SENT = "sent", "Sent"
    ACCEPTED = "accepted", "Accepted"
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

    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="ledger_entry",
    )
    settlement = models.ForeignKey(
        "settlements.ProviderSettlement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )

    currency = models.CharField(max_length=3, default="CAD")

    # Snapshot final (cents)
    gross_cents = models.IntegerField(default=0)
    tax_cents = models.IntegerField(default=0)
    fee_cents = models.IntegerField(default=0)

    net_provider_cents = models.IntegerField(default=0)
    platform_revenue_cents = models.IntegerField(default=0)

    fee_payer = models.CharField(
        max_length=8,
        choices=FEE_PAYER_CHOICES,
        default=FEE_PAYER_CLIENT,
    )

    tax_region_code = models.CharField(max_length=8, blank=True, null=True)

    is_adjustment = models.BooleanField(default=False, db_index=True)
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
        constraints = [
            models.UniqueConstraint(
                fields=["job"],
                condition=Q(is_adjustment=False),
                name="uniq_base_ledger_per_job",
            ),
            models.UniqueConstraint(
                fields=["job"],
                condition=Q(is_final=True, is_adjustment=False),
                name="uniq_final_ledger_per_job",
            ),
        ]
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


class JobLocation(models.Model):
    job = models.OneToOneField(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="location",
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
    )
    grid_lat = models.IntegerField(
        null=True,
        blank=True,
    )
    grid_lng = models.IntegerField(
        null=True,
        blank=True,
    )
    postal_code = models.CharField(
        max_length=10,
    )
    city = models.CharField(
        max_length=120,
    )
    province = models.CharField(
        max_length=10,
    )
    country = models.CharField(
        max_length=50,
        default="Canada",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["grid_lat", "grid_lng"], name="ix_job_location_grid"),
        ]

    def save(self, *args, **kwargs):
        if self.latitude is None or self.longitude is None:
            self.grid_lat = None
            self.grid_lng = None
        else:
            from providers.utils_geo_grid import compute_geo_grid

            self.grid_lat, self.grid_lng = compute_geo_grid(
                self.latitude,
                self.longitude,
            )
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Location for Job {self.job_id}"
