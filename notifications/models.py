from django.conf import settings
from django.db import models


class PushDevice(models.Model):
    class Role(models.TextChoices):
        CLIENT = "client", "Client"
        PROVIDER = "provider", "Provider"
        WORKER = "worker", "Worker"

    class Platform(models.TextChoices):
        IOS = "ios", "iOS"
        ANDROID = "android", "Android"
        WEB = "web", "Web"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_devices",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    platform = models.CharField(max_length=20, choices=Platform.choices)
    token = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "notifications_push_device"
        indexes = [
            models.Index(fields=["user", "role", "is_active"]),
            models.Index(fields=["platform", "is_active"]),
        ]

    def __str__(self):
        return f"{self.user_id} {self.role} {self.platform}"


class PushDispatchAttempt(models.Model):
    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        STUB_SENT = "stub_sent", "Stub sent"
        FAILED = "failed", "Failed"

    job_event = models.ForeignKey(
        "jobs.JobEvent",
        on_delete=models.CASCADE,
        related_name="push_dispatch_attempts",
    )
    device = models.ForeignKey(
        "notifications.PushDevice",
        on_delete=models.CASCADE,
        related_name="dispatch_attempts",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.STUB_SENT,
    )
    payload_json = models.JSONField(default=dict, blank=True)
    response_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notifications_push_dispatch_attempt"
        indexes = [
            models.Index(fields=["job_event", "created_at"]),
            models.Index(fields=["device", "created_at"]),
        ]

    def __str__(self):
        return f"{self.job_event_id} -> {self.device_id} ({self.status})"
