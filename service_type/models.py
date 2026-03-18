import re

from django.db import models
from django.utils.text import slugify


class ServiceType(models.Model):
    service_type_id = models.AutoField(primary_key=True)

    name = models.CharField(max_length=120, unique=True)
    name_en = models.CharField(max_length=120, blank=True, null=True)
    name_fr = models.CharField(max_length=120, blank=True, null=True)
    name_es = models.CharField(max_length=120, blank=True, null=True)
    slug = models.SlugField(
        max_length=100,
        unique=True,
        db_index=True,
        null=True,   # kept nullable in DB for legacy test rows; enforced at app level
        blank=False,
    )
    description = models.TextField(blank=True, null=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "service_type"
        ordering = ["name"]

    _TRAILING_TOKEN_RE = re.compile(r"^(?P<label>.+?)\s+[0-9a-f]{8}$", re.IGNORECASE)

    @classmethod
    def _sanitize_display_name(cls, raw_name):
        candidate = (raw_name or "").strip()
        if not candidate:
            return ""
        match = cls._TRAILING_TOKEN_RE.match(candidate)
        if match:
            return match.group("label").strip()
        return candidate

    def _get_localized_name(self):
        from django.utils.translation import get_language

        lang = (get_language() or "").lower()
        if lang.startswith("fr"):
            return self._sanitize_display_name(self.name_fr or self.name_en or self.name)
        if lang.startswith("es"):
            return self._sanitize_display_name(self.name_es or self.name_en or self.name)

        return self._sanitize_display_name(self.name_en or self.name)

    @property
    def localized_name(self):
        return self._get_localized_name()

    @property
    def display_name(self):
        return self.localized_name

    def __str__(self) -> str:
        return self.localized_name

    def clean(self):
        """Validate that no name fields contain trailing hash/token artifacts."""
        from django.core.exceptions import ValidationError

        errors = {}
        for field_name in ("name", "name_en", "name_fr", "name_es"):
            value = getattr(self, field_name, None)
            if value and self._TRAILING_TOKEN_RE.match(value):
                match = self._TRAILING_TOKEN_RE.match(value)
                cleaned = match.group("label").strip()
                errors[field_name] = (
                    f"Name contains trailing hash/token artifact. "
                    f"Found: '{value}'. Use cleaned form: '{cleaned}'"
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Auto-generate slug from name_en (fallback: name) on first save."""
        if not self.slug:
            source = (self.name_en or self.name or "").strip()
            self.slug = slugify(source) or None
        super().save(*args, **kwargs)


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
