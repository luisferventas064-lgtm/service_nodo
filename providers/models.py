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