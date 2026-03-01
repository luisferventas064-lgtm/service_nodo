from django.contrib import admin

from .models import SecurityEvent


@admin.register(SecurityEvent)
class SecurityEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_type",
        "phone_number",
        "actor_type",
        "actor_id",
        "ip_address",
        "created_at",
    )
    list_filter = ("event_type", "actor_type", "created_at")
    search_fields = ("phone_number", "=actor_id", "ip_address")
    ordering = ("-created_at",)
