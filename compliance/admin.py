from django.contrib import admin

from .models import ComplianceRule


@admin.register(ComplianceRule)
class ComplianceRuleAdmin(admin.ModelAdmin):
    list_display = (
        "province_code",
        "service_type",
        "certificate_required",
        "insurance_required",
        "is_mandatory",
    )
    list_filter = (
        "province_code",
        "certificate_required",
        "insurance_required",
        "is_mandatory",
    )
    search_fields = ("certificate_name", "notes", "service_type__name")
    ordering = ("province_code", "service_type__name", "certificate_name")

