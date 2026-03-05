from django.contrib import admin
from .models import RequiredCertification, ServiceSkill, ServiceType


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


@admin.register(RequiredCertification)
class RequiredCertificationAdmin(admin.ModelAdmin):
    list_display = (
        "service_type",
        "province",
        "requires_certificate",
        "certificate_type",
        "requires_insurance",
        "created_at",
    )
    list_filter = ("province", "requires_certificate", "requires_insurance", "service_type")
    search_fields = ("service_type__name", "province", "certificate_type")
    ordering = ("service_type__name", "province")

