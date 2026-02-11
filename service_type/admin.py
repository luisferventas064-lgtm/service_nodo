from django.contrib import admin
from .models import ServiceType, ServiceSkill, ProviderServiceType


@admin.register(ServiceType)
class ServiceTypeAdmin(admin.ModelAdmin):
    list_display = ("service_type_id", "name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(ServiceSkill)
class ServiceSkillAdmin(admin.ModelAdmin):
    list_display = ("service_skill_id", "service_type", "name", "is_required", "created_at")
    list_filter = ("is_required", "service_type")
    search_fields = ("name", "service_type__name")
    ordering = ("service_type__name", "name")


@admin.register(ProviderServiceType)
class ProviderServiceTypeAdmin(admin.ModelAdmin):
    list_display = (
        "provider_service_type_id",
        "provider",
        "service_type",
        "price_type",
        "base_price",
        "is_active",
        "created_at",
    )
    list_filter = ("price_type", "is_active", "service_type")
    search_fields = ("provider__email", "provider__company_name", "service_type__name")
    ordering = ("-created_at",)
