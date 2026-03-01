from django.db import models

PROVINCE_CHOICES = [
    ("QC", "Québec"),
]

QC_CITY_CHOICES = [
    ("Montreal", "Montréal"),
    ("Laval", "Laval"),
    ("Longueuil", "Longueuil"),
    ("Brossard", "Brossard"),
    ("Saint-Lambert", "Saint-Lambert"),
    ("Terrebonne", "Terrebonne"),
    ("Mascouche", "Mascouche"),
    ("Repentigny", "Repentigny"),
    ("Blainville", "Blainville"),
    ("Boisbriand", "Boisbriand"),
    ("Rosemere", "Rosemère"),
    ("Lorraine", "Lorraine"),
    ("Sainte-Therese", "Sainte-Thérèse"),
    ("Mirabel", "Mirabel"),
    ("Saint-Eustache", "Saint-Eustache"),
    ("Deux-Montagnes", "Deux-Montagnes"),
    ("Sainte-Marthe-sur-le-Lac", "Sainte-Marthe-sur-le-Lac"),
    ("Pointe-Calumet", "Pointe-Calumet"),
    ("Oka", "Oka"),
    ("Saint-Jerome", "Saint-Jérôme"),
]


class Worker(models.Model):
    worker_id = models.BigAutoField(primary_key=True)

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)

    phone_number = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(unique=True)

    preferred_language = models.CharField(max_length=50, blank=True, null=True)

    country = models.CharField(max_length=100, default="Canada")

    # Importante: NO reducimos tamaño todavía (evita truncation en SQL Server)
    province = models.CharField(
        max_length=100,
        choices=PROVINCE_CHOICES,
        blank=True,
        null=True,
    )
    city = models.CharField(
        max_length=100,
        choices=QC_CITY_CHOICES,
        blank=True,
        null=True,
    )

    postal_code = models.CharField(max_length=20, null=True, blank=True)
    address_line1 = models.CharField(max_length=255, null=True, blank=True)

    availability_mode = models.CharField(max_length=20, default="manual")
    is_available_now = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    disputes_lost_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "worker"
        ordering = ["last_name", "first_name"]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}"
