from datetime import timedelta

from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from assignments.models import JobAssignment
from .activity_financial_adapter import build_activity_financial_data_map
from .activity_financials import (
    build_activity_analytics,
    build_monthly_revenue,
)
from .dto.activity_row_dto import ActivityRowDTO
from .models import Job


ACTIVITY_STATUS_CHOICES = (
    ("all", _("All")),
    (Job.JobStatus.POSTED, _("Posted")),
    (Job.JobStatus.ASSIGNED, _("Assigned")),
    (Job.JobStatus.IN_PROGRESS, _("In progress")),
    ("completed", _("Completed")),
    (Job.JobStatus.CANCELLED, _("Cancelled")),
)

ACTIVITY_STATUS_FILTERS = {
    "all": None,
    Job.JobStatus.POSTED: (Job.JobStatus.POSTED,),
    Job.JobStatus.ASSIGNED: (Job.JobStatus.ASSIGNED,),
    Job.JobStatus.IN_PROGRESS: (Job.JobStatus.IN_PROGRESS,),
    "completed": (
        Job.JobStatus.COMPLETED,
        Job.JobStatus.CONFIRMED,
    ),
    Job.JobStatus.CANCELLED: (Job.JobStatus.CANCELLED,),
}

ACTIVITY_COUNTERPARTY_LABELS = {
    "client": _("Provider"),
    "provider": _("Client"),
    "worker": _("Client"),
}
DATE_RANGE_CHOICES = (
    ("", _("All time")),
    ("today", _("Today")),
    ("7d", _("Last 7 days")),
    ("30d", _("Last 30 days")),
)
SORT_CHOICES = (
    ("newest", _("Newest first")),
    ("oldest", _("Oldest first")),
    ("status", _("Status A-Z")),
)
PAGE_SIZE = 10
ACTIVITY_SELECT_RELATED = (
    "client",
    "service_type",
    "selected_provider",
    "hold_worker",
    "provider_service",
)
ACTIVITY_ONLY_FIELDS = (
    "job_id",
    "job_status",
    "created_at",
    "city",
    "province",
    "postal_code",
    "is_asap",
    "scheduled_date",
    "scheduled_start_time",
    "provider_service_name_snapshot",
    "cancelled_by",
    "cancel_reason",
    "requested_subservice_base_price_snapshot",
    "requested_quantity_snapshot",
    "requested_unit_price_snapshot",
    "requested_base_line_total_snapshot",
    "requested_subtotal_snapshot",
    "requested_total_snapshot",
    "quoted_total_price_cents",
    "client_id",
    "client__first_name",
    "client__last_name",
    "service_type_id",
    "service_type__name",
    "service_type__name_en",
    "service_type__name_fr",
    "service_type__name_es",
    "selected_provider_id",
    "selected_provider__company_name",
    "selected_provider__contact_first_name",
    "selected_provider__contact_last_name",
    "hold_worker_id",
    "hold_worker__first_name",
    "hold_worker__last_name",
    "provider_service_id",
    "provider_service__custom_name",
)
ACTIVE_ASSIGNMENT_EXISTS = JobAssignment.objects.filter(
    job_id=OuterRef("pk"),
    is_active=True,
)


class ActivityQuery:
    def __init__(self, actor_type, actor, params=None, *, limit=PAGE_SIZE):
        if actor is None:
            raise ValueError("Activity actor is required.")

        self.actor_type = actor_type
        self.role = actor_type
        self.actor = actor
        self.params = params or {}
        self.limit = limit or PAGE_SIZE
        self.selected_status = self._normalize_selected_status(
            self._get_param("status")
        )
        self.selected_range = self._normalize_selected_range(
            self._get_param("range")
        )
        self.selected_sort = self._normalize_selected_sort(
            self._get_param("sort")
        )

    def _get_param(self, key, default=None):
        getter = getattr(self.params, "get", None)
        if getter is None:
            return default
        return getter(key, default)

    def _get_filter_kwargs(self):
        if self.actor_type == "client":
            return Q(client=self.actor)
        if self.actor_type == "provider":
            return (
                Q(assignments__provider=self.actor, assignments__is_active=True)
                | Q(selected_provider=self.actor, has_active_assignment=False)
            )
        if self.actor_type == "worker":
            return (
                Q(assignments__worker=self.actor, assignments__is_active=True)
                | Q(hold_worker=self.actor, has_active_assignment=False)
            )
        raise ValueError(f"Unsupported activity actor type: {self.actor_type!r}")

    def _normalize_selected_status(self, selected_status):
        normalized = (selected_status or "all").strip().lower()
        if normalized not in ACTIVITY_STATUS_FILTERS:
            return "all"
        return normalized

    def _normalize_selected_range(self, selected_range):
        normalized = (selected_range or "").strip().lower()
        valid_ranges = {choice[0] for choice in DATE_RANGE_CHOICES}
        if normalized not in valid_ranges:
            return ""
        return normalized

    def _normalize_selected_sort(self, selected_sort):
        normalized = (selected_sort or "newest").strip().lower()
        valid_sorts = {choice[0] for choice in SORT_CHOICES}
        if normalized not in valid_sorts:
            return "newest"
        return normalized

    def base_queryset(self):
        queryset = (
            Job.objects.annotate(
                has_active_assignment=Exists(ACTIVE_ASSIGNMENT_EXISTS)
            )
            .filter(self._get_filter_kwargs())
            .select_related(*ACTIVITY_SELECT_RELATED)
            .only(*ACTIVITY_ONLY_FIELDS)
            .prefetch_related(
                Prefetch(
                    "assignments",
                    queryset=JobAssignment.objects.filter(is_active=True).select_related(
                        "provider",
                        "worker",
                    ),
                    to_attr="activity_active_assignments",
                )
            )
        )
        if self.actor_type in {"provider", "worker"}:
            queryset = queryset.distinct()
        return queryset

    def apply_status_filter(self, queryset):
        status_filter = ACTIVITY_STATUS_FILTERS[self.selected_status]
        if status_filter is None:
            return queryset
        return queryset.filter(job_status__in=status_filter)

    def get_selected_range(self):
        return self.selected_range

    def get_selected_sort(self):
        return self.selected_sort

    def apply_date_range_filter(self, queryset):
        selected_range = self.get_selected_range()
        if not selected_range:
            return queryset

        now = timezone.now()
        if selected_range == "today":
            return queryset.filter(created_at__date=now.date())
        if selected_range == "7d":
            return queryset.filter(created_at__gte=now - timedelta(days=7))
        if selected_range == "30d":
            return queryset.filter(created_at__gte=now - timedelta(days=30))
        return queryset

    def apply_ordering(self, queryset):
        selected_sort = self.get_selected_sort()
        if selected_sort == "oldest":
            return queryset.order_by("created_at", "job_id")
        if selected_sort == "status":
            return queryset.order_by("job_status", "-created_at", "-job_id")
        return queryset.order_by("-created_at", "-job_id")

    def get_page_number(self):
        return self._get_param("page") or 1

    def get_page_size(self):
        return self.limit

    def get_paginated_page(self, queryset):
        paginator = Paginator(queryset, self.get_page_size())
        return paginator.get_page(self.get_page_number())

    def get_counts(self, queryset=None):
        source_queryset = queryset if queryset is not None else self.base_queryset()
        return {
            row["job_status"]: row["count"]
            for row in (
                source_queryset.order_by().values("job_status")
                .annotate(count=Count("pk"))
            )
        }

    def get_status_choices(self, queryset=None):
        raw_status_counts = self.get_counts(queryset)
        total_jobs = sum(raw_status_counts.values())
        status_choices = []
        for status_value, status_label in ACTIVITY_STATUS_CHOICES:
            status_filter = ACTIVITY_STATUS_FILTERS[status_value]
            if status_filter is None:
                count = total_jobs
            else:
                count = sum(raw_status_counts.get(status, 0) for status in status_filter)
            status_choices.append(
                {
                    "value": status_value,
                    "label": status_label,
                    "count": count,
                }
            )
        return status_choices

    def get_date_range_choices(self):
        return DATE_RANGE_CHOICES

    def get_sort_choices(self):
        return SORT_CHOICES

    def get_filtered_queryset(self):
        queryset = self.base_queryset()
        queryset = self.apply_date_range_filter(queryset)
        queryset = self.apply_status_filter(queryset)
        queryset = self.apply_ordering(queryset)
        return queryset

    def get_analytics(self, jobs=None):
        source_jobs = list(jobs) if jobs is not None else list(self.get_filtered_queryset())
        return build_activity_analytics(self.actor_type, source_jobs)

    def get_monthly_revenue(self, jobs=None):
        source_jobs = list(jobs) if jobs is not None else list(self.get_filtered_queryset())
        return build_monthly_revenue(source_jobs)

    def get_rows(self, queryset=None):
        queryset = list(queryset) if queryset is not None else list(self.get_filtered_queryset())
        financials_by_job = build_activity_financial_data_map(queryset, self.actor_type)
        page_obj = self.get_paginated_page(queryset)
        rows = [
            ActivityRowDTO.from_job(
                job,
                actor_type=self.actor_type,
                financial=financials_by_job.get(job.job_id),
            )
            for job in page_obj.object_list
        ]
        return rows, page_obj

    def build_context(self):
        base_queryset = self.base_queryset()
        ranged_queryset = self.apply_date_range_filter(base_queryset)
        ordered_jobs = list(self.get_filtered_queryset())
        financials_by_job = build_activity_financial_data_map(
            ordered_jobs,
            self.actor_type,
        )
        page_obj = self.get_paginated_page(ordered_jobs)
        rows = [
            ActivityRowDTO.from_job(
                job,
                actor_type=self.actor_type,
                financial=financials_by_job.get(job.job_id),
            )
            for job in page_obj.object_list
        ]
        activity_financial_headers = ActivityRowDTO.get_financial_headers(self.actor_type)
        show_activity_payment_status = self.actor_type == "client"

        return {
            "jobs": rows,
            "activity_rows": rows,
            "page_obj": page_obj,
            "is_paginated": page_obj.paginator.num_pages > 1,
            "selected_status": self.selected_status,
            "selected_range": self.get_selected_range(),
            "selected_sort": self.get_selected_sort(),
            "status_choices": self.get_status_choices(ranged_queryset),
            "date_range_choices": self.get_date_range_choices(),
            "sort_choices": self.get_sort_choices(),
            "role": self.role,
            "activity_actor_type": self.actor_type,
            "activity_counterparty_label": ACTIVITY_COUNTERPARTY_LABELS[self.actor_type],
            "activity_analytics": build_activity_analytics(
                self.actor_type,
                ordered_jobs,
                financials_by_job=financials_by_job,
            ),
            "show_activity_payment_status": show_activity_payment_status,
            "activity_financial_headers": activity_financial_headers,
            "activity_table_colspan": (
                7
                + len(activity_financial_headers)
                + (1 if show_activity_payment_status else 0)
            ),
        }
