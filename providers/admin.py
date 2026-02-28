from django.contrib import admin

from .models import (
    ProviderService,
    ServiceCategory,
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


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug", "is_active")
    search_fields = ("name", "slug")
    list_filter = ("is_active",)


@admin.register(ProviderService)
class ProviderServiceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "provider",
        "category",
        "custom_name",
        "billing_unit",
        "price_cents",
        "is_active",
        "created_at",
    )
    search_fields = (
        "custom_name",
        "provider__company_name",
        "provider__contact_first_name",
        "provider__contact_last_name",
        "provider__email",
        "category__name",
    )
    list_filter = ("billing_unit", "is_active", "category")


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
