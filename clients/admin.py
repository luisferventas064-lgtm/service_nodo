from django.contrib import admin

from .models import Client, ClientInvoiceSequence, ClientTicket


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("client_id", "first_name", "last_name", "email", "phone_number", "is_active")
    search_fields = ("first_name", "last_name", "email", "phone_number")
    list_filter = ("is_active", "province", "city")


@admin.register(ClientInvoiceSequence)
class ClientInvoiceSequenceAdmin(admin.ModelAdmin):
    list_display = ("client", "prefix", "next_number", "created_at")
    search_fields = ("client__email", "client__first_name", "client__last_name", "prefix")


@admin.register(ClientTicket)
class ClientTicketAdmin(admin.ModelAdmin):
    list_display = (
        "client",
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
    search_fields = ("ticket_no", "client__email")
    list_filter = ("stage", "status", "ref_type", "currency")
