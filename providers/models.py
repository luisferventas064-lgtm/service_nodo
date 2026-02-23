from django.db import models
from django.utils import timezone


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
    tax_cents = models.IntegerField(default=0)
    line_total_cents = models.IntegerField(default=0)  # subtotal + tax

    tax_region_code = models.CharField(max_length=16, blank=True, default="")  # ej: CA-QC
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

