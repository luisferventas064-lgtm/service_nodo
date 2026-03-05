from django.db import models


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


class RequiredCertification(models.Model):
    service_type = models.ForeignKey(
        "service_type.ServiceType",
        on_delete=models.CASCADE,
        related_name="regulatory_requirements",
    )
    province = models.CharField(max_length=100)
    requires_certificate = models.BooleanField(default=False)
    certificate_type = models.CharField(max_length=100, blank=True)
    requires_insurance = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("service_type", "province")

    def __str__(self):
        return f"{self.service_type.name} ({self.province})"


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
