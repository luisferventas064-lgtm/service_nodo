from django.db import models

from clients.models import Client
from providers.models import Provider
from service_type.models import ServiceType


class Job(models.Model):
    class JobStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    job_id = models.AutoField(primary_key=True)

    job_status = models.CharField(
        max_length=20,
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

    expires_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "jobs_job"

    def __str__(self):
        return f"Job {self.job_id} - {self.job_status} - {self.city}"
