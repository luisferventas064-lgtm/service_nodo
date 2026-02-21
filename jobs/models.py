from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from clients.models import Client
from providers.models import Provider
from service_type.models import ServiceType


class Job(models.Model):
    class JobStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        PENDING_PROVIDER_CONFIRMATION = "pending_provider_confirmation", "Pending provider confirmation"
        PENDING_CLIENT_CONFIRMATION = "pending_client_confirmation", "Pending client confirmation"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"
        # HOLD (URGENCIA)
    hold_provider = models.ForeignKey(
        "providers.Provider",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="held_jobs",
    )
    hold_expires_at = models.DateTimeField(null=True, blank=True)

    # Precio final congelado (URGENCIA)
    quoted_urgent_total_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    quoted_urgent_fee_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    def is_hold_active(self) -> bool:
        return bool(self.hold_provider_id and self.hold_expires_at and self.hold_expires_at > timezone.now())


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

    # ---------------------------
    # Price snapshot (NORMAL + URGENCIA)
    # NORMAL usa base. URGENCIA puede usar base + emergency.
    # ---------------------------
    quoted_service_skill_id = models.IntegerField(null=True, blank=True)

    quoted_base_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    quoted_currency_code = models.CharField(max_length=3, default="CAD")
    quoted_pricing_unit = models.CharField(max_length=20, default="fixed")

    quoted_emergency_fee_type = models.CharField(
        max_length=10, default="none"
    )  # none|fixed|percent
    quoted_emergency_fee_value = models.DecimalField(
        max_digits=10, decimal_places=2, default="0.00"
    )

    expires_at = models.DateTimeField(blank=True, null=True)
    next_alert_at = models.DateTimeField(null=True, blank=True, db_index=True)
    alert_attempts = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()

    def save(self, *args, **kwargs):
        now = timezone.now()

        # Si NO hay fecha -> ON_DEMAND (ASAP)
        if not self.scheduled_date:
            self.job_mode = self.JobMode.ON_DEMAND
            self.is_asap = True
        else:
            # Si no hay hora, asumimos 00:00
            service_time = self.scheduled_start_time or datetime.min.time()
            service_dt = datetime.combine(self.scheduled_date, service_time)
            service_dt = timezone.make_aware(service_dt, timezone.get_current_timezone())

            # Regla 48 horas
            if service_dt - now <= timedelta(hours=48):
                self.job_mode = self.JobMode.ON_DEMAND
                self.is_asap = True
            else:
                self.job_mode = self.JobMode.SCHEDULED
                self.is_asap = False

        self.full_clean()
        super().save(*args, **kwargs)

        if self.job_mode == self.JobMode.ON_DEMAND:
            if self.is_asap is not True:
                raise ValidationError(
                    {"is_asap": "For ON_DEMAND jobs, is_asap must be True."}
                )
            return

        if self.job_mode == self.JobMode.SCHEDULED:
            if self.is_asap is not False:
                raise ValidationError(
                    {"is_asap": "For SCHEDULED jobs, is_asap must be False."}
                )
            if not self.scheduled_date:
                raise ValidationError(
                    {"scheduled_date": "scheduled_date is required for SCHEDULED jobs."}
                )

    class Meta:
        db_table = "jobs_job"

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


# Backward-compatible aliases (si otros archivos importan estos nombres)
JobStatus = Job.JobStatus
JobMode = Job.JobMode
