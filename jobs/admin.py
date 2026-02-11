from django.contrib import admin
from .models import Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = (
        "job_id",
        "job_status",
        "selected_provider",
        "service_type",
        "city",
        "province",
        "created_at",
        "schedule_display",
    

    )

    list_filter = (
        "job_status",
        "province",
        "city",
    )

    search_fields = (
        "job_id",
        "selected_provider__company_name",
        "selected_provider__contact_first_name",
        "selected_provider__contact_last_name",
        "city",
        "province",
    )

    ordering = ("-created_at",)
    @admin.display(description="Schedule")
    def schedule_display(self, obj: Job) -> str:
        if obj.is_asap:
            return "ASAP"
        if obj.scheduled_date and obj.scheduled_start_time:
            return f"{obj.scheduled_date} {obj.scheduled_start_time}"
        if obj.scheduled_date:
            return f"{obj.scheduled_date}"
        return "Scheduled (no date)"
