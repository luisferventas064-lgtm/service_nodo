from django.contrib import admin

from .models import (
    Provider,
    ProviderBillingProfile,
    ProviderCertificate,
    ProviderInvoiceSequence,
    ProviderServiceArea,
    ProviderTicket,
)


class ProviderCertificateInline(admin.TabularInline):
    model = ProviderCertificate
    extra = 0


class ProviderBillingProfileInline(admin.StackedInline):
    model = ProviderBillingProfile
    extra = 0
    max_num = 1


class ProviderInvoiceSequenceInline(admin.StackedInline):
    model = ProviderInvoiceSequence
    extra = 0
    max_num = 1


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = (
        "provider_id",
        "provider_type",
        "company_name",
        "contact_first_name",
        "contact_last_name",
        "email",
        "is_active",
    )
    search_fields = ("company_name", "contact_first_name", "contact_last_name", "email")
    list_filter = ("provider_type", "is_active", "province", "city")
    inlines = [ProviderBillingProfileInline, ProviderInvoiceSequenceInline, ProviderCertificateInline]


@admin.register(ProviderServiceArea)
class ProviderServiceAreaAdmin(admin.ModelAdmin):
    list_display = ("provider_service_area_id", "provider", "city", "province", "is_active")
    list_filter = ("province", "is_active")
    search_fields = ("city", "province", "provider__company_name", "provider__email")


@admin.register(ProviderTicket)
class ProviderTicketAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "ticket_no",
        "stage",
        "status",
        "ref_type",
        "ref_id",
        "subtotal_cents",
        "tax_cents",
        "total_cents",
        "currency",
        "tax_region_code",
        "created_at",
    )
    search_fields = ("ticket_no",)
    list_filter = ("stage", "status", "ref_type", "currency")
