from django.db import models


class Provider(models.Model):
    provider_id = models.AutoField(primary_key=True)

    PROVIDER_TYPE_CHOICES = [
        ("self_employed", "Self-employed"),
        ("company", "Company"),
    ]

    provider_type = models.CharField(max_length=20, choices=PROVIDER_TYPE_CHOICES)

    company_name = models.CharField(max_length=255, blank=True, null=True)
    contact_first_name = models.CharField(max_length=100)
    contact_last_name = models.CharField(max_length=100)

    phone_number = models.CharField(max_length=20)
    email = models.EmailField(unique=True)

    country = models.CharField(max_length=100, default="Canada")
    province = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    address_line1 = models.CharField(max_length=255)

    service_radius_km = models.PositiveIntegerField(default=10)

    availability_mode = models.CharField(max_length=20, default="manual")
    is_available_now = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "provider"

    def __str__(self) -> str:
        if self.company_name:
            return self.company_name
        return f"{self.contact_first_name} {self.contact_last_name}"


class ProviderServiceArea(models.Model):
    provider_service_area_id = models.AutoField(primary_key=True)

    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.CASCADE,
        db_column="provider_id",
    )

    city = models.CharField(max_length=100)
    province = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "provider_service_area"

    def __str__(self) -> str:
        return f"{self.provider} - {self.city}, {self.province}"


class ProviderServiceType(models.Model):
    provider_service_type_id = models.AutoField(primary_key=True)

    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.CASCADE,
        db_column="provider_id",
        related_name="provider_service_types",
    )

    service_type = models.ForeignKey(
        "service_type.ServiceType",
        on_delete=models.CASCADE,
        db_column="service_type_id",
        related_name="provider_service_types",
    )

    price_type = models.CharField(max_length=20)
    base_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "provider_service_type"

    def __str__(self) -> str:
        return f"{self.provider} - {self.service_type} ({self.price_type})"
class PricingUnit(models.TextChoices):
    FIXED = "fixed", "Fixed"
    HOURLY = "hourly", "Hourly"
    SQM = "sqm", "Per m²"
    LINEAR_FT = "linear_ft", "Per linear ft"
    ITEM = "item", "Per item"


class ProviderSkillPrice(models.Model):
    emergency_fee_type = models.CharField(max_length=10, default="none")  # none|fixed|percent
    emergency_fee_value = models.DecimalField(max_digits=10, decimal_places=2, default="0.00")

    provider_skill_price_id = models.BigAutoField(primary_key=True)

    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.CASCADE,
        related_name="skill_prices",
        db_index=True,
    )
    service_skill = models.ForeignKey(
        "service_type.ServiceSkill",
        on_delete=models.CASCADE,
        related_name="provider_prices",
        db_index=True,
    )

    price_amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency_code = models.CharField(max_length=3, default="CAD")

    pricing_unit = models.CharField(
        max_length=20,
        choices=PricingUnit.choices,
        default=PricingUnit.FIXED,
    )
    min_qty = models.PositiveSmallIntegerField(default=1)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "provider_skill_price"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "service_skill"],
                name="uq_provider_skill_price_provider_skill",
            )
        ]
        indexes = [
            models.Index(fields=["provider", "is_active"], name="ix_psp_provider_active"),
            models.Index(fields=["service_skill", "is_active"], name="ix_psp_skill_active"),
        ]

    def __str__(self) -> str:
        return f"{self.provider_id} / {self.service_skill_id} = {self.price_amount} {self.currency_code}"

