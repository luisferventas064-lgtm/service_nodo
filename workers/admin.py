from django.contrib import admin
from .models import Worker


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = (
        "worker_id",
        "first_name",
        "last_name",
        "email",
        "phone_number",
        "city",
        "province",
        "is_available_now",
        "is_active",
        "created_at",
    )



    list_filter = (
        "province",
        "city",
        "is_available_now",
        "is_active",
    )

    search_fields = (
        "first_name",
        "last_name",
        "email",
        "phone_number",
        "city",
        "province",
    )

    ordering = ("-created_at",)

