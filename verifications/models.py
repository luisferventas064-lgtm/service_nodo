import uuid

from django.db import models
from django.utils import timezone


class PhoneVerification(models.Model):
    class ActorType(models.TextChoices):
        CLIENT = "client", "Client"
        PROVIDER = "provider", "Provider"

    verification_id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    actor_type = models.CharField(
        max_length=20,
        choices=ActorType.choices,
    )
    actor_id = models.IntegerField()
    phone_number = models.CharField(max_length=20)
    code_hash = models.CharField(max_length=128)
    attempts = models.PositiveIntegerField(default=0)
    is_verified = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "phone_verification"
        indexes = [
            models.Index(fields=["actor_type", "actor_id"]),
            models.Index(fields=["phone_number"]),
        ]


class SecurityEvent(models.Model):
    class EventType(models.TextChoices):
        OTP_IP_RATE_LIMIT = "OTP_IP_RATE_LIMIT", "OTP IP Rate Limit"
        OTP_PHONE_RATE_LIMIT = "OTP_PHONE_RATE_LIMIT", "OTP Phone Rate Limit"
        OTP_DAILY_LIMIT = "OTP_DAILY_LIMIT", "OTP Daily Limit"
        OTP_REQUEST_COOLDOWN = "OTP_REQUEST_COOLDOWN", "OTP Request Cooldown"
        OTP_ABUSE_BLOCK = "OTP_ABUSE_BLOCK", "OTP Abuse Block"

    event_type = models.CharField(max_length=50, choices=EventType.choices)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    actor_type = models.CharField(max_length=20, null=True, blank=True)
    actor_id = models.IntegerField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "security_event"
        indexes = [
            models.Index(fields=["event_type"]),
            models.Index(fields=["phone_number"]),
            models.Index(fields=["created_at"]),
        ]
