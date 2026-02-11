from django.db import models
from providers.models import Provider


class ServiceType(models.Model):
    service_type_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True, null=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "service_type"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ServiceSkill(models.Model):
    service_skill_id = models.AutoField(primary_key=True)

    service_type = models.ForeignKey(
        ServiceType,
        on_delete=models.CASCADE,
        related_name="skills",
    )

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, null=True)
    is_required = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "service_skill"
        unique_together = ("service_type", "name")

    def __str__(self) -> str:
        return f"{self.service_type.name} - {self.name}"


class ProviderServiceType(models.Model):
    provider_service_type_id = models.AutoField(primary_key=True)

    provider = models.ForeignKey(
        Provider,
        on_delete=models.CASCADE,
        related_name="provider_services",
    )

    service_type = models.ForeignKey(
        ServiceType,
        on_delete=models.PROTECT,
        related_name="provider_services",
    )

    PRICE_TYPE_CHOICES = [
        ("hourly", "Hourly"),
        ("fixed", "Fixed"),
    ]

    price_type = models.CharField(
        max_length=10,
        choices=PRICE_TYPE_CHOICES,
        default="hourly",
    )

    base_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "provider_service_type"
        unique_together = ("provider", "service_type")

    def __str__(self) -> str:
        return f"{self.provider} -> {self.service_type.name}"
