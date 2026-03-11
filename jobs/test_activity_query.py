from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from assignments.models import JobAssignment
from clients.models import Client
from jobs.activity_query import ActivityQuery
from jobs.dto.activity_row_dto import ActivityRowDTO
from jobs.models import Job
from providers.models import Provider
from service_type.models import ServiceType
from workers.models import Worker


class ActivityQueryTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(
            first_name="Query",
            last_name="Client",
            email="query.client@test.local",
            phone_number="+15145551201",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1 Query St",
        )
        self.provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Query Provider",
            contact_first_name="Query",
            contact_last_name="Provider",
            phone_number="+15145551202",
            email="query.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="2 Query St",
        )
        self.service_type = ServiceType.objects.create(
            name="Query Service",
            description="Query Service",
        )
 
    def _create_job(self, *, status, offer_name, city="Montreal", cancelled=False):
        job_kwargs = {
            "client": self.client_obj,
            "selected_provider": self.provider,
            "service_type": self.service_type,
            "provider_service_name_snapshot": "Posted Query Offer",
            "job_mode": Job.JobMode.ON_DEMAND,
            "job_status": status,
            "is_asap": True,
            "country": "Canada",
            "province": "QC",
            "city": city,
            "postal_code": "H1A1A1",
            "address_line1": "3 Query St",
        }
        job_kwargs["provider_service_name_snapshot"] = offer_name
        if cancelled:
            job_kwargs["cancelled_by"] = Job.CancellationActor.CLIENT
            job_kwargs["cancel_reason"] = Job.CancelReason.CLIENT_CANCELLED
        return Job.objects.create(**job_kwargs)

    def _set_created_at(self, job, dt):
        Job.objects.filter(pk=job.pk).update(created_at=dt)

    def _create_provider(self, *, email, first_name, last_name):
        return Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name=f"{first_name} {last_name} Legal",
            contact_first_name=first_name,
            contact_last_name=last_name,
            phone_number=f"+1514{Provider.objects.count() + 1000000:07d}",
            email=email,
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="2 Provider St",
        )

    def _create_worker(self, *, email, first_name, last_name):
        return Worker.objects.create(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone_number=f"+1438{Worker.objects.count() + 1000000:07d}",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A3",
            address_line1="4 Worker St",
        )

    def create_activity_jobs(self, total=50, status=Job.JobStatus.POSTED):
        now = timezone.now()
        jobs = []
        for index in range(total):
            created_at = now - timedelta(minutes=index)
            job_kwargs = {
                "client": self.client_obj,
                "selected_provider": self.provider,
                "service_type": self.service_type,
                "provider_service_name_snapshot": f"Bulk {status} Offer {index}",
                "job_mode": Job.JobMode.ON_DEMAND,
                "job_status": status,
                "is_asap": True,
                "country": "Canada",
                "province": "QC",
                "city": "Montreal",
                "postal_code": "H1A1A1",
                "address_line1": f"{index} Bulk St",
                "created_at": created_at,
                "updated_at": created_at,
            }
            if status == Job.JobStatus.CANCELLED:
                job_kwargs["cancelled_by"] = Job.CancellationActor.CLIENT
                job_kwargs["cancel_reason"] = Job.CancelReason.CLIENT_CANCELLED
            jobs.append(Job(**job_kwargs))

        Job.objects.bulk_create(jobs)

    def test_activity_query_applies_status_filter_and_builds_counts(self):
        posted_job = self._create_job(
            status=Job.JobStatus.POSTED,
            offer_name="Posted Query Offer",
        )
        self._create_job(
            status=Job.JobStatus.COMPLETED,
            offer_name="Completed Query Offer",
            city="Laval",
        )

        query = ActivityQuery("client", self.client_obj, params={"status": "posted"})

        rows, page_obj = query.get_rows()
        status_choices = query.get_status_choices()

        self.assertEqual(query.selected_status, "posted")
        self.assertEqual(len(rows), 1)
        self.assertEqual(page_obj.number, 1)
        self.assertIsInstance(rows[0], ActivityRowDTO)
        self.assertEqual(rows[0].job_id, posted_job.job_id)
        self.assertEqual(rows[0].status_label, "Posted")
        self.assertEqual(
            status_choices,
            [
                {"value": "all", "label": "All", "count": 2},
                {"value": "posted", "label": "Posted", "count": 1},
                {"value": "assigned", "label": "Assigned", "count": 0},
                {"value": "in_progress", "label": "In progress", "count": 0},
                {"value": "completed", "label": "Completed", "count": 1},
                {"value": "cancelled", "label": "Cancelled", "count": 0},
            ],
        )

    def test_build_context_returns_page_obj(self):
        self._create_job(status=Job.JobStatus.POSTED, offer_name="One")

        context = ActivityQuery("client", self.client_obj).build_context()

        self.assertIn("page_obj", context)
        self.assertEqual(context["page_obj"].number, 1)
        self.assertFalse(context["is_paginated"])

    def test_base_queryset_selects_activity_relations_and_limits_columns(self):
        queryset = ActivityQuery("client", self.client_obj).base_queryset()

        self.assertEqual(
            queryset.query.select_related,
            {
                "client": {},
                "service_type": {},
                "selected_provider": {},
                "hold_worker": {},
                "provider_service": {},
            },
        )
        self.assertEqual(queryset.query.deferred_loading[1], False)
        self.assertIn("job_id", queryset.query.deferred_loading[0])
        self.assertIn("service_type__name", queryset.query.deferred_loading[0])
        self.assertIn("selected_provider__contact_first_name", queryset.query.deferred_loading[0])

    def test_build_context_still_returns_rows_after_queryset_optimization(self):
        job = self._create_job(
            status=Job.JobStatus.CANCELLED,
            offer_name="Optimized Offer",
            cancelled=True,
        )

        context = ActivityQuery("client", self.client_obj).build_context()

        self.assertIn("jobs", context)
        self.assertEqual(len(context["jobs"]), 1)
        self.assertEqual(context["jobs"][0].job_id, job.job_id)
        self.assertEqual(context["jobs"][0].status_note, "Client - Client cancelled")

    def test_build_context_paginates_rows(self):
        for index in range(12):
            self._create_job(
                status=Job.JobStatus.POSTED,
                offer_name=f"Offer {index}",
            )

        context = ActivityQuery(
            "client",
            self.client_obj,
            params={"page": 2},
        ).build_context()

        self.assertTrue(context["is_paginated"])
        self.assertEqual(context["page_obj"].number, 2)
        self.assertEqual(context["page_obj"].paginator.num_pages, 2)
        self.assertEqual(len(context["jobs"]), 2)
        self.assertEqual(context["jobs"][0].service_name, "Query Service")
        self.assertIsInstance(context["jobs"][0], ActivityRowDTO)

    def test_build_context_with_bulk_dataset_paginates(self):
        self.create_activity_jobs(total=35, status=Job.JobStatus.POSTED)

        context = ActivityQuery(
            "client",
            self.client_obj,
            params={},
        ).build_context()

        self.assertTrue(context["is_paginated"])
        self.assertEqual(context["page_obj"].number, 1)
        self.assertEqual(len(context["jobs"]), 10)
        self.assertGreater(context["page_obj"].paginator.count, 10)

    def test_build_context_with_bulk_dataset_returns_second_page(self):
        self.create_activity_jobs(total=35, status=Job.JobStatus.POSTED)

        context = ActivityQuery(
            "client",
            self.client_obj,
            params={"page": 2},
        ).build_context()

        self.assertEqual(context["page_obj"].number, 2)
        self.assertGreater(len(context["jobs"]), 0)

    def test_build_context_with_bulk_dataset_filters_status_correctly(self):
        self.create_activity_jobs(total=20, status=Job.JobStatus.POSTED)
        self.create_activity_jobs(total=12, status=Job.JobStatus.COMPLETED)

        context = ActivityQuery(
            "client",
            self.client_obj,
            params={"status": "completed"},
        ).build_context()

        self.assertEqual(context["selected_status"], "completed")
        self.assertEqual(context["page_obj"].paginator.count, 12)
        for row in context["jobs"]:
            self.assertEqual(row.status, Job.JobStatus.COMPLETED)

    def test_build_context_with_bulk_dataset_returns_status_counts(self):
        self.create_activity_jobs(total=20, status=Job.JobStatus.POSTED)
        self.create_activity_jobs(total=12, status=Job.JobStatus.COMPLETED)

        context = ActivityQuery(
            "client",
            self.client_obj,
            params={},
        ).build_context()
        status_counts = {
            choice["value"]: choice["count"]
            for choice in context["status_choices"]
        }

        self.assertEqual(status_counts["all"], 32)
        self.assertEqual(status_counts["posted"], 20)
        self.assertEqual(status_counts["completed"], 12)

    def test_build_context_keeps_selected_status_with_pagination(self):
        for index in range(11):
            self._create_job(
                status=Job.JobStatus.POSTED,
                offer_name=f"Posted Offer {index}",
            )
        self._create_job(
            status=Job.JobStatus.CANCELLED,
            offer_name="Cancelled Query Offer",
            cancelled=True,
        )

        context = ActivityQuery(
            "client",
            self.client_obj,
            params={"status": "posted", "page": 2},
        ).build_context()

        self.assertEqual(context["selected_status"], "posted")
        self.assertEqual(context["page_obj"].number, 2)
        self.assertEqual(len(context["jobs"]), 1)
        self.assertEqual(context["jobs"][0].status, Job.JobStatus.POSTED)

    def test_build_context_filters_today(self):
        today_job = self._create_job(
            status=Job.JobStatus.POSTED,
            offer_name="Today Offer",
        )
        old_job = self._create_job(
            status=Job.JobStatus.POSTED,
            offer_name="Old Offer",
        )
        self._set_created_at(today_job, timezone.now())
        self._set_created_at(old_job, timezone.now() - timedelta(days=2))

        context = ActivityQuery(
            "client",
            self.client_obj,
            params={"range": "today"},
        ).build_context()

        self.assertEqual(context["selected_range"], "today")
        self.assertEqual(len(context["jobs"]), 1)
        self.assertEqual(context["jobs"][0].service_option_name, "Today Offer")
        self.assertEqual(context["status_choices"][0]["count"], 1)

    def test_build_context_filters_last_7_days(self):
        recent_job = self._create_job(
            status=Job.JobStatus.POSTED,
            offer_name="Recent Offer",
        )
        old_job = self._create_job(
            status=Job.JobStatus.POSTED,
            offer_name="Very Old Offer",
        )
        self._set_created_at(recent_job, timezone.now() - timedelta(days=3))
        self._set_created_at(old_job, timezone.now() - timedelta(days=9))

        context = ActivityQuery(
            "client",
            self.client_obj,
            params={"range": "7d"},
        ).build_context()

        self.assertEqual(context["selected_range"], "7d")
        self.assertEqual(len(context["jobs"]), 1)
        self.assertEqual(context["jobs"][0].service_option_name, "Recent Offer")
        self.assertEqual(context["status_choices"][0]["count"], 1)

    def test_build_context_preserves_selected_range(self):
        self._create_job(
            status=Job.JobStatus.POSTED,
            offer_name="Range Offer",
        )

        context = ActivityQuery(
            "client",
            self.client_obj,
            params={"range": "7d"},
        ).build_context()

        self.assertEqual(context["selected_range"], "7d")
        self.assertEqual(
            context["date_range_choices"],
            (
                ("", "All time"),
                ("today", "Today"),
                ("7d", "Last 7 days"),
                ("30d", "Last 30 days"),
            ),
        )

    def test_build_context_defaults_to_newest_sort(self):
        self._create_job(
            status=Job.JobStatus.POSTED,
            offer_name="Newest Offer",
        )

        context = ActivityQuery("client", self.client_obj, params={}).build_context()

        self.assertEqual(context["selected_sort"], "newest")

    def test_apply_ordering_oldest_orders_oldest_first(self):
        older_job = self._create_job(
            status=Job.JobStatus.POSTED,
            offer_name="Older Offer",
        )
        newer_job = self._create_job(
            status=Job.JobStatus.POSTED,
            offer_name="Newer Offer",
        )
        self._set_created_at(older_job, timezone.now() - timedelta(days=5))
        self._set_created_at(newer_job, timezone.now() - timedelta(days=1))

        query = ActivityQuery("client", self.client_obj, params={"sort": "oldest"})
        ordered_queryset = query.apply_ordering(query.base_queryset())

        self.assertEqual(query.get_selected_sort(), "oldest")
        self.assertEqual(list(ordered_queryset.values_list("job_id", flat=True)[:2]), [older_job.job_id, newer_job.job_id])

    def test_build_context_preserves_selected_sort(self):
        self._create_job(
            status=Job.JobStatus.POSTED,
            offer_name="Sorted Offer",
        )

        context = ActivityQuery(
            "client",
            self.client_obj,
            params={"sort": "status"},
        ).build_context()

        self.assertEqual(context["selected_sort"], "status")

    def test_build_context_query_count_stays_reasonable(self):
        self._create_job(
            status=Job.JobStatus.POSTED,
            offer_name="Query Count Offer",
        )

        query = ActivityQuery(
            actor_type="client",
            actor=self.client_obj,
            params={},
        )

        with CaptureQueriesContext(connection) as ctx:
            context = query.build_context()

        self.assertIn("jobs", context)
        self.assertLessEqual(len(ctx), 12)

    def test_build_context_with_filters_query_count_stays_reasonable(self):
        recent_completed_job = self._create_job(
            status=Job.JobStatus.COMPLETED,
            offer_name="Filtered Count Offer",
        )
        old_completed_job = self._create_job(
            status=Job.JobStatus.COMPLETED,
            offer_name="Old Filtered Count Offer",
        )
        self._set_created_at(recent_completed_job, timezone.now() - timedelta(days=2))
        self._set_created_at(old_completed_job, timezone.now() - timedelta(days=40))

        query = ActivityQuery(
            actor_type="client",
            actor=self.client_obj,
            params={
                "status": "completed",
                "range": "30d",
            },
        )

        with CaptureQueriesContext(connection) as ctx:
            context = query.build_context()

        self.assertIn("jobs", context)
        self.assertLessEqual(len(ctx), 14)

    def test_provider_query_ignores_selected_provider_once_active_assignment_exists(self):
        assigned_provider = self._create_provider(
            email="assigned.provider@test.local",
            first_name="Assigned",
            last_name="Provider",
        )
        job = self._create_job(
            status=Job.JobStatus.ASSIGNED,
            offer_name="Assigned Offer",
        )
        JobAssignment.objects.create(
            job=job,
            provider=assigned_provider,
            is_active=True,
        )

        stale_rows, _ = ActivityQuery("provider", self.provider).get_rows()
        assigned_rows, _ = ActivityQuery("provider", assigned_provider).get_rows()

        self.assertEqual(stale_rows, [])
        self.assertEqual(len(assigned_rows), 1)
        self.assertEqual(assigned_rows[0].job_id, job.job_id)

    def test_provider_query_and_client_row_use_active_assignment_when_selected_provider_is_cleared(self):
        job = self._create_job(
            status=Job.JobStatus.ASSIGNED,
            offer_name="Assignment Fallback Offer",
        )
        job.selected_provider = None
        job.save(update_fields=["selected_provider", "updated_at"])
        JobAssignment.objects.create(
            job=job,
            provider=self.provider,
            is_active=True,
        )

        provider_rows, _ = ActivityQuery("provider", self.provider).get_rows()
        client_rows, _ = ActivityQuery("client", self.client_obj).get_rows()

        self.assertEqual(len(provider_rows), 1)
        self.assertEqual(provider_rows[0].job_id, job.job_id)
        self.assertEqual(client_rows[0].counterparty_display, "Query Provider")
        self.assertEqual(client_rows[0].provider_name, "Query Provider")

    def test_provider_row_uses_active_assignment_worker_name_when_hold_worker_is_empty(self):
        worker = self._create_worker(
            email="assigned.worker@test.local",
            first_name="Assigned",
            last_name="Worker",
        )
        job = self._create_job(
            status=Job.JobStatus.ASSIGNED,
            offer_name="Worker Name Fallback Offer",
        )
        JobAssignment.objects.create(
            job=job,
            provider=self.provider,
            worker=worker,
            is_active=True,
        )

        rows, _ = ActivityQuery("provider", self.provider).get_rows()

        self.assertEqual(rows[0].worker_name, "Assigned Worker")

    def test_worker_query_ignores_hold_worker_once_active_assignment_exists(self):
        stale_worker = self._create_worker(
            email="stale.worker@test.local",
            first_name="Stale",
            last_name="Worker",
        )
        assigned_worker = self._create_worker(
            email="active.worker@test.local",
            first_name="Active",
            last_name="Worker",
        )
        job = self._create_job(
            status=Job.JobStatus.ASSIGNED,
            offer_name="Worker Assignment Offer",
        )
        job.hold_worker = stale_worker
        job.selected_provider = None
        job.save(update_fields=["hold_worker", "selected_provider", "updated_at"])
        JobAssignment.objects.create(
            job=job,
            provider=self.provider,
            worker=assigned_worker,
            is_active=True,
        )

        stale_rows, _ = ActivityQuery("worker", stale_worker).get_rows()
        assigned_rows, _ = ActivityQuery("worker", assigned_worker).get_rows()

        self.assertEqual(stale_rows, [])
        self.assertEqual(len(assigned_rows), 1)
        self.assertEqual(assigned_rows[0].job_id, job.job_id)
        self.assertEqual(assigned_rows[0].provider_name, "Query Provider")
