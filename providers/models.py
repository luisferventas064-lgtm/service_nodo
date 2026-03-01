from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class Provider(models.Model):
    provider_id = models.AutoField(primary_key=True)

    TYPE_SELF_EMPLOYED = "self_employed"
    TYPE_COMPANY = "company"

    PROVIDER_TYPE_CHOICES = [
        (TYPE_SELF_EMPLOYED, "Self-employed"),
        (TYPE_COMPANY, "Company"),
    ]

    provider_type = models.CharField(max_length=20, choices=PROVIDER_TYPE_CHOICES)

    company_name = models.CharField(max_length=255, blank=True, null=True)
    legal_name = models.CharField(max_length=255, blank=True, default="")
    business_registration_number = models.CharField(max_length=100, blank=True, default="")
    contact_first_name = models.CharField(max_length=100)
    contact_last_name = models.CharField(max_length=100)

    phone_number = models.CharField(max_length=20)
    email = models.EmailField(unique=True)
    is_phone_verified = models.BooleanField(default=False)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    phone_verification_attempts = models.IntegerField(default=0)
    profile_completed = models.BooleanField(default=True)
    service_area = models.CharField(max_length=255, blank=True, default="")
    accepts_terms = models.BooleanField(default=True)
    billing_profile_completed = models.BooleanField(default=True)

    stripe_account_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
    )
    stripe_onboarding_completed = models.BooleanField(default=False)
    stripe_account_status = models.CharField(
        max_length=50,
        null=True,
        blank=True,
    )
    stripe_details_submitted_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    stripe_charges_enabled = models.BooleanField(default=False)
    stripe_payouts_enabled = models.BooleanField(default=False)

    country = models.CharField(max_length=100, default="Canada")
    province = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    zone = models.ForeignKey(
        "providers.ServiceZone",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="providers",
    )
    postal_code = models.CharField(max_length=20)
    address_line1 = models.CharField(max_length=255)

    service_radius_km = models.PositiveIntegerField(default=10)

    availability_mode = models.CharField(max_length=20, default="manual")
    is_available_now = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)

    # Marketplace metrics (persisted)
    completed_jobs_count = models.PositiveIntegerField(default=0)
    cancelled_jobs_count = models.PositiveIntegerField(default=0)
    disputes_lost_count = models.PositiveIntegerField(default=0)
    quality_warning_active = models.BooleanField(default=False)
    restricted_until = models.DateTimeField(null=True, blank=True)
    avg_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0.00)

    # Trust / differentiation
    is_verified = models.BooleanField(default=False)

    # Optional but recommended
    acceptance_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "provider"
        indexes = [
            models.Index(fields=["province", "city", "is_active"], name="ix_provider_geo_active"),
        ]

    def __str__(self) -> str:
        if self.company_name:
            return self.company_name
        return f"{self.contact_first_name} {self.contact_last_name}"

    @property
    def normalized_provider_type(self) -> str:
        if self.provider_type == self.TYPE_COMPANY:
            return "company"
        return "individual"

    @property
    def contact_person_name(self) -> str:
        return f"{self.contact_first_name} {self.contact_last_name}".strip()

    def has_active_service(self) -> bool:
        return self.services.filter(is_active=True).exists()

    @property
    def is_fully_active(self) -> bool:
        if self.normalized_provider_type == "individual":
            return (
                self.is_phone_verified
                and self.profile_completed
                and self.billing_profile_completed
                and self.accepts_terms
                and self.has_active_service()
            )

        if self.normalized_provider_type == "company":
            return (
                self.is_phone_verified
                and self.profile_completed
                and self.billing_profile_completed
                and self.accepts_terms
                and self.has_active_service()
            )

        return False

    @property
    def is_operational(self) -> bool:
        return self.is_fully_active


class ServiceZone(models.Model):
    province = models.CharField(max_length=50)
    city = models.CharField(max_length=100)
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["province", "city", "name"],
                name="uq_servicezone_province_city_name",
            )
        ]
        indexes = [
            models.Index(fields=["province", "city"], name="ix_servicezone_prov_city"),
        ]
        ordering = ["province", "city", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.province})"


class MarketplaceAnalyticsSnapshot(models.Model):
    marketplace_analytics_snapshot_id = models.BigAutoField(primary_key=True)
    captured_at = models.DateTimeField(auto_now_add=True, db_index=True)
    snapshot_version = models.CharField(max_length=50, default="ANALYTICS_V1")
    snapshot = models.TextField()

    class Meta:
        db_table = "marketplace_analytics_snapshot"
        ordering = ["-captured_at"]

    def __str__(self) -> str:
        timestamp = self.captured_at.strftime("%Y-%m-%d %H:%M:%S") if self.captured_at else "pending"
        return f"Marketplace snapshot {timestamp}"


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


class ServiceCategory(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class ProviderService(models.Model):
    BILLING_UNIT_CHOICES = [
        ("hour", "Per Hour"),
        ("fixed", "Fixed Price"),
        ("sqm", "Per Square Meter"),
        ("km", "Per Kilometer"),
        ("day", "Per Day"),
    ]

    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.CASCADE,
        related_name="services",
    )

    category = models.ForeignKey(
        "providers.ServiceCategory",
        on_delete=models.PROTECT,
    )

    custom_name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    billing_unit = models.CharField(max_length=20, choices=BILLING_UNIT_CHOICES)
    price_cents = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["category", "is_active"], name="ix_psvc_cat_active"),
            models.Index(fields=["price_cents"], name="ix_psvc_price"),
        ]

    def __str__(self):
        return f"{self.provider_id} - {self.custom_name}"


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


class ProviderCertificate(models.Model):
    provider_certificate_id = models.BigAutoField(primary_key=True)

    class Status(models.TextChoices):
        DECLARED = "declared", "Declared"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.CASCADE,
        db_column="provider_id",
        related_name="certificates",
        db_index=True,
    )

    cert_type = models.CharField(max_length=80)          # ej: RBQ, WHMIS, First Aid
    cert_name = models.CharField(max_length=150, blank=True, default="")
    taken_at = models.CharField(max_length=150, blank=True, default="")  # dónde lo tomó (escuela/centro)

    issued_by = models.CharField(max_length=150, blank=True, default="")
    issued_country = models.CharField(max_length=80, blank=True, default="")
    issued_city = models.CharField(max_length=80, blank=True, default="")

    issued_date = models.DateField(null=True, blank=True)
    expires_date = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DECLARED)
    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "provider_certificate"
        indexes = [
            models.Index(fields=["provider", "status"], name="ix_prov_cert_status"),
            models.Index(fields=["cert_type"], name="ix_prov_cert_type"),
        ]

    def __str__(self) -> str:
        return f"{self.provider_id} {self.cert_type} ({self.status})"


class ProviderBillingProfile(models.Model):
    provider_billing_profile_id = models.BigAutoField(primary_key=True)

    class EntityType(models.TextChoices):
        SELF_EMPLOYED = "self_employed", "Self-employed"
        COMPANY = "company", "Company"

    provider = models.OneToOneField(
        "providers.Provider",
        on_delete=models.CASCADE,
        db_column="provider_id",
        related_name="billing_profile",
    )

    entity_type = models.CharField(max_length=20, choices=EntityType.choices)

    legal_name = models.CharField(max_length=200, blank=True, default="")
    business_name = models.CharField(max_length=200, blank=True, default="")

    gst_hst_number = models.CharField(max_length=40, blank=True, default="")
    qst_tvq_number = models.CharField(max_length=40, blank=True, default="")
    neq_number = models.CharField(max_length=40, blank=True, default="")
    bn_number = models.CharField(max_length=40, blank=True, default="")

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "provider_billing_profile"

    def __str__(self) -> str:
        return f"{self.provider_id} {self.entity_type}"


class ProviderInvoiceSequence(models.Model):
    provider_invoice_sequence_id = models.BigAutoField(primary_key=True)

    provider = models.OneToOneField(
        "providers.Provider",
        on_delete=models.CASCADE,
        db_column="provider_id",
        related_name="invoice_seq",
    )

    prefix = models.CharField(max_length=30, blank=True, default="")
    next_number = models.BigIntegerField(default=1)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "provider_invoice_sequence"

    def __str__(self) -> str:
        return f"{self.provider_id} {self.prefix}{self.next_number}"


class ProviderTicket(models.Model):
    provider_ticket_id = models.BigAutoField(primary_key=True)

    class Stage(models.TextChoices):
        ESTIMATE = "estimate", "Estimate"
        FINAL = "final", "Final"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        FINALIZED = "finalized", "Finalized"
        VOID = "void", "Void"

    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.CASCADE,
        db_column="provider_id",
        related_name="tickets",
        db_index=True,
    )

    # Por ahora lo dejamos generico, porque puede ser job o assignment.
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
        db_table = "provider_ticket"
        constraints = [
            models.UniqueConstraint(fields=["provider", "ticket_no"], name="uq_provider_ticket_no"),
            models.UniqueConstraint(fields=["provider", "ref_type", "ref_id"], name="uq_provider_ticket_ref"),
        ]
        indexes = [
            models.Index(fields=["provider", "created_at"], name="ix_provider_ticket_created"),
        ]

    def __str__(self) -> str:
        return f"{self.provider_id} {self.ticket_no} {self.stage} {self.ref_type}:{self.ref_id}"


class ProviderTicketLine(models.Model):
    class LineType(models.TextChoices):
        BASE = "base", "Base service"
        EXTRA = "extra", "Extra"
        FEE = "fee", "Fee"
        ADJUST = "adjust", "Adjustment"

    ticket = models.ForeignKey(
        "providers.ProviderTicket",
        on_delete=models.CASCADE,
        related_name="lines",
    )

    line_no = models.PositiveIntegerField()  # 1..N dentro del ticket
    line_type = models.CharField(max_length=16, choices=LineType.choices)

    description = models.CharField(max_length=200)
    qty = models.DecimalField(max_digits=10, decimal_places=2, default=1)

    unit_price_cents = models.IntegerField(default=0)
    line_subtotal_cents = models.IntegerField(default=0)  # qty * unit_price
    tax_rate_bps = models.IntegerField(default=0)
    tax_cents = models.IntegerField(default=0)
    line_total_cents = models.IntegerField(default=0)  # subtotal + tax

    tax_region_code = models.CharField(max_length=10, null=True, blank=True)  # ej: CA-QC
    tax_code = models.CharField(max_length=32, blank=True, default="")  # ej: GST/QST snapshot

    meta = models.JSONField(default=dict, blank=True)  # para futuro (skill, fee model, etc.)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ticket", "line_no"],
                name="uq_provider_ticket_line_no_per_ticket",
            ),
        ]
        indexes = [
            models.Index(fields=["ticket", "line_type"], name="ix_provider_line_ticket_type"),
        ]

    def clean(self):
        super().clean()
        if self.ticket_id and getattr(self.ticket, "stage", None) == "final":
            raise ValidationError("ProviderTicket is final; lines are immutable.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.ticket_id and getattr(self.ticket, "stage", None) == "final":
            raise ValidationError("ProviderTicket is final; lines are immutable.")
        return super().delete(*args, **kwargs)


class ProviderUser(models.Model):
    ROLE_CHOICES = (
        ("owner", "Owner"),
        ("finance", "Finance"),
        ("worker", "Worker"),
    )

    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.CASCADE,
        related_name="provider_users",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="provider_roles",
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "user"],
                name="uq_provider_user_unique"
            )
        ]

    def __str__(self):
        return f"{self.provider_id} - {self.user_id} ({self.role})"


class ProviderReview(models.Model):
    provider_review_id = models.BigAutoField(primary_key=True)

    job = models.OneToOneField(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="provider_review",
    )
    provider = models.ForeignKey(
        "providers.Provider",
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.CASCADE,
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        if self.job_id:
            job_status = getattr(self.job, "job_status", None)
            if job_status and job_status != "confirmed":
                raise ValidationError("Job must be confirmed to create a review.")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("ProviderReview is immutable once created.")
        self.full_clean()
        return super().save(*args, **kwargs)

