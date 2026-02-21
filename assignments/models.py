from django.db import models, transaction
from django.db.models import Q
from jobs.models import Job
from providers.models import Provider
from workers.models import Worker


class JobAssignment(models.Model):
    ASSIGNMENT_STATUS_CHOICES = [
        ("assigned", "Assigned"),
        ("accepted", "Accepted"),
        ("in_progress", "In progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
        ("expired", "Expired"),
    ]

    assignment_id = models.BigAutoField(primary_key=True)

    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        db_column="job_id",
        related_name="assignments",
    )

    provider = models.ForeignKey(
        Provider,
        on_delete=models.PROTECT,
        db_column="provider_id",
        null=True,
        blank=True,
        related_name="job_assignments",
    )

    worker = models.ForeignKey(
        Worker,
        on_delete=models.PROTECT,
        db_column="worker_id",
        null=True,
        blank=True,
        related_name="job_assignments",
    )

    assignment_status = models.CharField(
        max_length=20,
        choices=ASSIGNMENT_STATUS_CHOICES,
        default="assigned",
    )

    # NUEVO: solo un assignment activo por job
    is_active = models.BooleanField(default=True)

    assigned_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "job_assignment"
        constraints = [
            models.UniqueConstraint(
                fields=["job"],
                condition=Q(is_active=True),
                name="uq_job_assignment_one_active_per_job",
            )
        ]

    def save(self, *args, **kwargs):
        """
        Garantiza que solo exista un assignment activo por job.
        """
        with transaction.atomic():
            super().save(*args, **kwargs)

            if self.is_active:
                JobAssignment.objects.filter(
                    job=self.job,
                    is_active=True
                ).exclude(
                    assignment_id=self.assignment_id
                ).update(is_active=False)


class AssignmentFee(models.Model):
    PAYER_NONE = "none"
    PAYER_PROVIDER = "provider"
    PAYER_CLIENT = "client"
    PAYER_SPONSOR = "sponsor"
    PAYER_CHOICES = [
        (PAYER_NONE, "None"),
        (PAYER_PROVIDER, "Provider"),
        (PAYER_CLIENT, "Client"),
        (PAYER_SPONSOR, "Sponsor"),
    ]

    MODEL_OFF = "off"
    MODEL_COMMISSION = "commission"
    MODEL_SUBSCRIPTION = "subscription"
    MODEL_ADS = "ads"
    MODEL_CHOICES = [
        (MODEL_OFF, "Off"),
        (MODEL_COMMISSION, "Commission"),
        (MODEL_SUBSCRIPTION, "Subscription"),
        (MODEL_ADS, "Ads"),
    ]

    STATUS_OFF = "off"
    STATUS_PENDING = "pending"
    STATUS_CHARGED = "charged"
    STATUS_WAIVED = "waived"
    STATUS_CHOICES = [
        (STATUS_OFF, "Off"),
        (STATUS_PENDING, "Pending"),
        (STATUS_CHARGED, "Charged"),
        (STATUS_WAIVED, "Waived"),
    ]

    assignment = models.OneToOneField(
        "assignments.JobAssignment",
        on_delete=models.CASCADE,
        related_name="fee",
    )

    payer = models.CharField(max_length=16, choices=PAYER_CHOICES, default=PAYER_NONE)
    model = models.CharField(max_length=16, choices=MODEL_CHOICES, default=MODEL_OFF)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OFF)
    amount_cents = models.IntegerField(default=0)
    currency = models.CharField(max_length=3, default="CAD")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assignment_fee"
