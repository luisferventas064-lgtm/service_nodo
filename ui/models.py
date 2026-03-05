from django.db import models
from django.utils import timezone


class PasswordResetCode(models.Model):
    phone_number = models.CharField(max_length=20)
    code = models.CharField(max_length=6)
    purpose = models.CharField(max_length=20, default="reset")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    attempts = models.IntegerField(default=0)
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["phone_number", "purpose", "created_at"]),
        ]
        ordering = ["-created_at"]

    def is_valid(self):
        return (timezone.now() - self.created_at).total_seconds() < 600
