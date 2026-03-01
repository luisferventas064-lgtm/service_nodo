import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Client(models.Model):
    client_id = models.BigAutoField(primary_key=True)

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)

    phone_number = models.CharField(max_length=20)
    email = models.EmailField()
    is_phone_verified = models.BooleanField(default=False)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    phone_verification_attempts = models.IntegerField(default=0)
    profile_completed = models.BooleanField(default=False)

    country = models.CharField(max_length=100)
    province = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    address_line1 = models.CharField(max_length=255)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "client"

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class ClientInvoiceSequence(models.Model):
    client_invoice_sequence_id = models.BigAutoField(primary_key=True)

    client = models.OneToOneField(
        "clients.Client",
        on_delete=models.CASCADE,
        db_column="client_id",
        related_name="invoice_seq",
    )

    prefix = models.CharField(max_length=30, blank=True, default="")
    next_number = models.BigIntegerField(default=1)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "client_invoice_sequence"

    def __str__(self) -> str:
        return f"{self.client_id} {self.prefix}{self.next_number}"


class ClientTicket(models.Model):
    client_ticket_id = models.BigAutoField(primary_key=True)

    class Stage(models.TextChoices):
        ESTIMATE = "estimate", "Estimate"
        FINAL = "final", "Final"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        FINALIZED = "finalized", "Finalized"
        VOID = "void", "Void"

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.CASCADE,
        db_column="client_id",
        related_name="tickets",
        db_index=True,
    )

    ref_type = models.CharField(max_length=30)  # "job" | "assignment"
    ref_id = models.BigIntegerField(db_index=True)

    ticket_no = models.CharField(max_length=60)
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.ESTIMATE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)

    subtotal_cents = models.BigIntegerField(default=0)
    tax_cents = models.BigIntegerField(default=0)
    total_cents = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="CAD")
    tax_region_code = models.CharField(max_length=20, blank=True, default="")
    snapshot_hash = models.CharField(max_length=64, null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "client_ticket"
        constraints = [
            models.UniqueConstraint(fields=["client", "ticket_no"], name="uq_client_ticket_no"),
            models.UniqueConstraint(fields=["client", "ref_type", "ref_id"], name="uq_client_ticket_ref"),
        ]
        indexes = [
            models.Index(fields=["client", "created_at"], name="ix_client_ticket_created"),
        ]

    def __str__(self) -> str:
        return f"{self.client_id} {self.ticket_no} {self.stage} {self.ref_type}:{self.ref_id}"

    def generate_snapshot_hash(self) -> str:
        lines = []
        for line in self.lines.order_by("line_no", "id"):
            lines.append(
                {
                    "line_no": int(line.line_no or 0),
                    "line_type": line.line_type,
                    "description": line.description,
                    "qty": str(line.qty),
                    "unit_price_cents": int(line.unit_price_cents or 0),
                    "line_subtotal_cents": int(line.line_subtotal_cents or 0),
                    "tax_cents": int(line.tax_cents or 0),
                    "line_total_cents": int(line.line_total_cents or 0),
                    "tax_region_code": line.tax_region_code or "",
                    "tax_code": line.tax_code or "",
                }
            )

        payload = {
            "subtotal_cents": int(self.subtotal_cents or 0),
            "tax_cents": int(self.tax_cents or 0),
            "total_cents": int(self.total_cents or 0),
            "currency": self.currency,
            "tax_region_code": self.tax_region_code or "",
            "lines": lines,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        was_finalized = False
        if self.pk:
            previous = ClientTicket.objects.get(pk=self.pk)
            if previous.status == self.Status.FINALIZED:
                was_finalized = True
                immutable_fields = [
                    "subtotal_cents",
                    "tax_cents",
                    "total_cents",
                    "currency",
                    "tax_region_code",
                ]
                for field in immutable_fields:
                    if getattr(previous, field) != getattr(self, field):
                        raise ValidationError(
                            f"Cannot modify financial field '{field}' on FINALIZED ticket."
                        )

                if self.status != previous.status:
                    raise ValidationError("Cannot change status of a FINALIZED ticket.")

                if self.stage != previous.stage:
                    raise ValidationError("Cannot change stage of a FINALIZED ticket.")

        result = super().save(*args, **kwargs)

        should_write_hash = (
            self.status == self.Status.FINALIZED
            and (not self.snapshot_hash or not was_finalized)
        )
        if should_write_hash:
            snapshot_hash = self.generate_snapshot_hash()
            if self.snapshot_hash != snapshot_hash:
                ClientTicket.objects.filter(pk=self.pk).update(snapshot_hash=snapshot_hash)
                self.snapshot_hash = snapshot_hash

        return result

    def delete(self, *args, **kwargs):
        if self.status == self.Status.FINALIZED:
            raise ValidationError("Cannot delete a FINALIZED ticket.")
        return super().delete(*args, **kwargs)


class ClientTicketLine(models.Model):
    class LineType(models.TextChoices):
        BASE = "base", "Base service"
        EXTRA = "extra", "Extra"
        FEE = "fee", "Fee"
        ADJUST = "adjust", "Adjustment"

    ticket = models.ForeignKey(
        "clients.ClientTicket",
        on_delete=models.CASCADE,
        related_name="lines",
    )

    line_no = models.PositiveIntegerField()
    line_type = models.CharField(max_length=16, choices=LineType.choices)

    description = models.CharField(max_length=200)
    qty = models.DecimalField(max_digits=10, decimal_places=2, default=1)

    unit_price_cents = models.IntegerField(default=0)
    line_subtotal_cents = models.IntegerField(default=0)
    tax_rate_bps = models.IntegerField(default=0)
    tax_cents = models.IntegerField(default=0)
    line_total_cents = models.IntegerField(default=0)

    tax_region_code = models.CharField(max_length=10, null=True, blank=True)
    tax_code = models.CharField(max_length=32, blank=True, default="")

    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ticket", "line_no"],
                name="uq_client_ticket_line_no_per_ticket",
            ),
        ]
        indexes = [
            models.Index(fields=["ticket", "line_type"], name="ix_client_line_ticket_type"),
        ]

    def clean(self):
        super().clean()
        if self.ticket_id:
            ticket_status = (
                ClientTicket.objects.filter(pk=self.ticket_id)
                .values_list("status", flat=True)
                .first()
            )
            if ticket_status == ClientTicket.Status.FINALIZED:
                raise ValidationError("Cannot modify lines of a FINALIZED ticket.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.ticket_id:
            ticket_status = (
                ClientTicket.objects.filter(pk=self.ticket_id)
                .values_list("status", flat=True)
                .first()
            )
            if ticket_status == ClientTicket.Status.FINALIZED:
                raise ValidationError("Cannot delete lines of a FINALIZED ticket.")
        return super().delete(*args, **kwargs)
