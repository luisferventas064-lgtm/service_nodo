from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class SettlementStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"
    CANCELLED = "cancelled", "Cancelled"


class ProviderSettlement(models.Model):
    # --- Identidad ---
    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.PROTECT,
        related_name="settlements",
    )

    period_start = models.DateTimeField()
    period_end = models.DateTimeField()

    currency = models.CharField(max_length=10)

    # --- Totales consolidados (snapshot) ---
    total_gross_cents = models.BigIntegerField(default=0)
    total_tax_cents = models.BigIntegerField(default=0)
    total_fee_cents = models.BigIntegerField(default=0)
    total_net_provider_cents = models.BigIntegerField(default=0)
    total_platform_revenue_cents = models.BigIntegerField(default=0)
    total_jobs = models.IntegerField(default=0)

    # --- Estado ---
    status = models.CharField(
        max_length=20,
        choices=SettlementStatus.choices,
        default=SettlementStatus.PENDING,
    )

    # --- Auditoria ---
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True)

    class Meta:
        db_table = "provider_settlement"
        ordering = ["-period_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "period_start", "period_end"],
                name="uq_provider_settlement_period",
            )
        ]

    def __str__(self):
        return (
            f"Settlement {self.provider_id} "
            f"{self.period_start.date()} - {self.period_end.date()} ({self.status})"
        )


class MonthlySettlementClose(models.Model):
    """
    Represents a financial monthly close.

    Can be:
    - Global close (provider = NULL, is_global=True)
    - Provider close (provider != NULL, is_global=False)
    """

    provider = models.ForeignKey(
        "providers.Provider",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="monthly_closes",
    )

    period_start = models.DateTimeField()
    period_end = models.DateTimeField()

    is_global = models.BooleanField(default=False)

    # Financial snapshot
    total_gross_cents = models.BigIntegerField(default=0)
    total_provider_cents = models.BigIntegerField(default=0)
    total_platform_revenue_cents = models.BigIntegerField(default=0)

    # Audit
    closed_at = models.DateTimeField(auto_now_add=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="monthly_closes",
    )

    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["period_start", "period_end"]),
            models.Index(fields=["provider"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "period_start", "period_end", "is_global"],
                name="uq_monthly_close_unique_scope",
            )
        ]

    def __str__(self):
        if self.is_global:
            return (
                f"Global Close {self.period_start.date()} "
                f"-> {self.period_end.date()}"
            )
        return (
            f"Provider {self.provider_id} Close "
            f"{self.period_start.date()} -> {self.period_end.date()}"
        )


class SettlementExportEvidence(models.Model):
    EVENT_TYPE_CHOICES = [
        ("SETTLEMENT_EXPORTED", "SETTLEMENT_EXPORTED"),
    ]

    MODE_CHOICES = [
        ("single", "single"),
        ("range", "range"),
    ]

    # --- Identificacion ---
    event_type = models.CharField(
        max_length=50,
        choices=EVENT_TYPE_CHOICES,
        default="SETTLEMENT_EXPORTED",
        editable=False,
    )

    mode = models.CharField(
        max_length=10,
        choices=MODE_CHOICES,
    )

    run_id = models.CharField(
        max_length=64,
        unique=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    # --- Scope ---
    settlement = models.ForeignKey(
        "settlements.ProviderSettlement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    from_date = models.DateField(null=True, blank=True)
    to_date = models.DateField(null=True, blank=True)
    settlements_count = models.PositiveIntegerField()

    # --- Metricas ---
    total_rows = models.PositiveIntegerField()

    total_gross_cents = models.BigIntegerField()
    total_tax_cents = models.BigIntegerField()
    total_fee_cents = models.BigIntegerField()
    total_net_provider_cents = models.BigIntegerField()
    total_platform_revenue_cents = models.BigIntegerField()

    currency = models.CharField(max_length=10)

    # --- Archivo ---
    file_path = models.TextField()
    file_name = models.CharField(max_length=255)
    file_size_bytes = models.BigIntegerField()

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        errors = {}

        # --- Validacion de scope ---
        if self.mode == "single":
            if not self.settlement:
                errors["settlement"] = "Settlement is required for single mode."
            if self.from_date or self.to_date:
                errors["from_date"] = "Range fields must be empty in single mode."
                errors["to_date"] = "Range fields must be empty in single mode."

            if self.settlements_count != 1:
                errors["settlements_count"] = "settlements_count must be 1 in single mode."

        elif self.mode == "range":
            if not self.from_date or not self.to_date:
                errors["from_date"] = "from_date and to_date are required in range mode."
            if self.settlement:
                errors["settlement"] = "Settlement must be null in range mode."

            if self.from_date and self.to_date:
                if self.from_date > self.to_date:
                    errors["to_date"] = "to_date must be >= from_date."

        # --- Validacion montos no negativos ---
        amount_fields = [
            "total_rows",
            "total_gross_cents",
            "total_tax_cents",
            "total_fee_cents",
            "total_net_provider_cents",
            "total_platform_revenue_cents",
            "file_size_bytes",
            "settlements_count",
        ]

        for field in amount_fields:
            value = getattr(self, field)
            if value is not None and value < 0:
                errors[field] = "Cannot be negative."

        if errors:
            raise ValidationError(errors)
