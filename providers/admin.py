from django.contrib import admin
from .models import Provider


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "provider_type",
        "city",
        "province",
        "is_available_now",
        "is_active",
        "created_at",
    )
    list_filter = ("provider_type", "province", "city", "is_active", "is_available_now")
    search_fields = ("company_name", "contact_first_name", "contact_last_name", "email", "phone_number")
    ordering = ("-created_at",)

from .models import ProviderServiceArea


@admin.register(ProviderServiceArea)
class ProviderServiceAreaAdmin(admin.ModelAdmin):
    list_display = ("provider_service_area_id", "provider", "city", "province", "is_active")
    list_filter = ("province", "is_active")
    search_fields = ("city", "province", "provider__company_name", "provider__email")

