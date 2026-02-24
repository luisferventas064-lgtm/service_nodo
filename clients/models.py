from django.db import models
from django.utils import timezone


class Client(models.Model):
    client_id = models.BigAutoField(primary_key=True)

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)

    phone_number = models.CharField(max_length=20)
    email = models.EmailField()

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
