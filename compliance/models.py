from django.db import models


class ComplianceRule(models.Model):
    province_code = models.CharField(max_length=10, db_index=True)
    service_type = models.ForeignKey(
        "service_type.ServiceType",
        on_delete=models.CASCADE,
        related_name="compliance_rules",
    )
    certificate_name = models.CharField(max_length=255, blank=True)
    insurance_required = models.BooleanField(default=False)
    certificate_required = models.BooleanField(default=False)
    is_mandatory = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "compliance_rule"
        ordering = ["province_code", "service_type_id", "certificate_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["province_code", "service_type", "certificate_name"],
                name="uq_compliance_rule_scope_certificate",
            )
        ]

    def save(self, *args, **kwargs):
        self.province_code = (self.province_code or "").strip().upper()
        self.certificate_name = (self.certificate_name or "").strip()
        return super().save(*args, **kwargs)

    def __str__(self):
        certificate_label = self.certificate_name or "General rule"
        return f"{self.province_code} - {self.service_type} - {certificate_label}"

