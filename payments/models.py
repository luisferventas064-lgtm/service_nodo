from django.core.exceptions import ValidationError
from django.db import models

from clients.models import ClientTicket


class StripeWebhookEvent(models.Model):
    event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=255)

    stripe_account_id = models.CharField(max_length=255, null=True, blank=True)

    payload = models.JSONField()

    processing_status = models.CharField(
        max_length=50,
        default="received",
    )

    error_message = models.TextField(null=True, blank=True)

    processed_at = models.DateTimeField(auto_now_add=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["event_type"]),
            models.Index(fields=["stripe_account_id"]),
        ]


class ClientPayment(models.Model):
    job = models.ForeignKey("jobs.Job", on_delete=models.PROTECT)

    stripe_payment_intent_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
    )
    stripe_charge_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
    )

    amount_cents = models.IntegerField()

    stripe_status = models.CharField(
        max_length=50,
        default="created",
        db_index=True,
    )

    stripe_environment = models.CharField(
        max_length=10,
        default="test",
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ClientCreditNote(models.Model):
    ticket = models.ForeignKey(
        ClientTicket,
        on_delete=models.PROTECT,
        related_name="credit_notes",
    )
    client_payment = models.ForeignKey(
        "payments.ClientPayment",
        on_delete=models.PROTECT,
        related_name="credit_notes",
    )
    amount_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=10, default="CAD")
    reason = models.TextField(blank=True, default="")
    stripe_refund_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
    )
    stripe_environment = models.CharField(
        max_length=10,
        default="test",
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["ticket", "created_at"]),
            models.Index(fields=["stripe_environment"]),
        ]

    def clean(self):
        super().clean()
        if self.amount_cents <= 0:
            raise ValidationError({"amount_cents": "amount_cents must be greater than zero."})
        if not (self.currency or "").strip():
            raise ValidationError({"currency": "currency is required."})
        if self.ticket_id:
            if self.ticket.status != ClientTicket.Status.FINALIZED:
                raise ValidationError({"ticket": "Credit note requires a FINALIZED ticket."})
            if self.ticket.stage != ClientTicket.Stage.FINAL:
                raise ValidationError({"ticket": "Credit note requires a FINAL ticket stage."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
