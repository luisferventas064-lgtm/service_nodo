from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.utils import translation
from django.utils import timezone

from core.legal_disclaimers import FINANCIAL_DISCLAIMER_SHORT
from clients.models import Client
from clients.models import ClientTicket
from assignments.models import JobAssignment
from jobs.activity_service import build_activity_view_context, export_activity_csv
from jobs.dto.activity_row_dto import ActivityRowDTO
from jobs.models import Job, PlatformLedgerEntry
from providers.models import Provider
from service_type.models import ServiceType
from workers.models import Worker


class EnglishLocaleTestMixin:
    def setUp(self):
        super().setUp()
        self._language_override = translation.override("en")
        self._language_override.__enter__()

    def tearDown(self):
        self._language_override.__exit__(None, None, None)
        super().tearDown()


class ActivityServiceTests(EnglishLocaleTestMixin, TestCase):
    def test_build_activity_view_context_returns_activity_row_dtos(self):
        client = Client.objects.create(
            first_name="DTO",
            last_name="Client",
            email="dto.client@test.local",
            phone_number="+15145551101",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1 Client St",
        )
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="DTO Provider",
            contact_first_name="DTO",
            contact_last_name="Provider",
            phone_number="+15145551102",
            email="dto.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="2 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="DTO Service",
            description="DTO Service",
        )
        Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="DTO Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.CANCELLED,
            cancelled_by=Job.CancellationActor.CLIENT,
            cancel_reason=Job.CancelReason.CLIENT_CANCELLED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="3 Job St",
        )

        context = build_activity_view_context("client", client)

        self.assertEqual(context["selected_status"], "all")
        self.assertEqual(context["activity_counterparty_label"], "Provider")
        self.assertEqual(len(context["jobs"]), 1)
        self.assertIsInstance(context["jobs"][0], ActivityRowDTO)
        self.assertEqual(context["jobs"][0].service_name, "DTO Service")
        self.assertEqual(context["jobs"][0].service_option_name, "DTO Offer")
        self.assertEqual(context["jobs"][0].status_label, "Cancelled")
        self.assertEqual(context["jobs"][0].status_note, "Client - Client cancelled")
        self.assertEqual(context["activity_analytics"]["total_jobs"], 1)
        self.assertEqual(context["activity_analytics"]["cancelled_jobs"], 1)
        self.assertEqual(context["activity_financial_headers"], ("Total charged",))
        self.assertTrue(context["show_activity_payment_status"])
        self.assertEqual(context["activity_table_colspan"], 9)
        self.assertEqual(
            context["financial_disclaimer_short"],
            FINANCIAL_DISCLAIMER_SHORT,
        )

    def test_build_activity_view_context_includes_financial_analytics(self):
        client = Client.objects.create(
            first_name="Analytics",
            last_name="Client",
            email="analytics.client@test.local",
            phone_number="+15145551103",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="4 Client St",
        )
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Analytics Provider",
            contact_first_name="Analytics",
            contact_last_name="Provider",
            phone_number="+15145551104",
            email="analytics.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="5 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Analytics Service",
            description="Analytics Service",
        )
        completed_job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Analytics Offer 1",
            requested_total_snapshot=Decimal("120.00"),
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="6 Job St",
        )
        confirmed_job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Analytics Offer 2",
            quoted_total_price_cents=8_000,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.CONFIRMED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="7 Job St",
        )
        Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Analytics Offer 3",
            requested_total_snapshot=Decimal("50.00"),
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.CANCELLED,
            cancelled_by=Job.CancellationActor.CLIENT,
            cancel_reason=Job.CancelReason.CLIENT_CANCELLED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A3",
            address_line1="8 Job St",
        )
        PlatformLedgerEntry.objects.create(
            job=completed_job,
            gross_cents=12_000,
            fee_cents=2_500,
            net_provider_cents=9_500,
            is_final=True,
        )
        PlatformLedgerEntry.objects.create(
            job=confirmed_job,
            gross_cents=8_000,
            fee_cents=1_000,
            net_provider_cents=7_000,
            is_final=True,
        )

        context = build_activity_view_context("client", client)

        analytics = context["activity_analytics"]
        self.assertEqual(analytics["total_jobs"], 3)
        self.assertEqual(analytics["completed_jobs"], 2)
        self.assertEqual(analytics["cancelled_jobs"], 1)
        self.assertEqual(analytics["total_charged"], Decimal("170.00"))
        self.assertEqual(analytics["total_charged_display"], "170.00")
        self.assertEqual(
            analytics["financial_cards"],
            [{"label": "Total charged", "value": "170.00"}],
        )

    def test_build_activity_view_context_uses_client_ticket_status_for_payment_label(self):
        client = Client.objects.create(
            first_name="Payments",
            last_name="Client",
            email="payments.client@test.local",
            phone_number="+15145551131",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="31 Client St",
        )
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Payments Provider",
            contact_first_name="Payments",
            contact_last_name="Provider",
            phone_number="+15145551132",
            email="payments.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="32 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Payments Service",
            description="Payments Service",
        )
        job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Payments Offer",
            requested_total_snapshot=Decimal("75.00"),
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.ASSIGNED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="33 Job St",
        )
        ClientTicket.objects.create(
            client=client,
            ref_type="job",
            ref_id=job.job_id,
            ticket_no="CT-PAY-001",
            status=ClientTicket.Status.FINALIZED,
            total_cents=7_500,
        )

        context = build_activity_view_context("client", client)

        self.assertEqual(context["jobs"][0].payment_label, "Finalized")
        self.assertTrue(context["jobs"][0].payment_recorded)

    def test_export_activity_csv_returns_csv_response(self):
        client = Client.objects.create(
            first_name="CSV",
            last_name="Client",
            email="csv.client@test.local",
            phone_number="+15145551111",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="11 Client St",
        )
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="CSV Provider",
            contact_first_name="CSV",
            contact_last_name="Provider",
            phone_number="+15145551112",
            email="csv.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="12 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="CSV Service",
            description="CSV Service",
        )
        worker = Worker.objects.create(
            first_name="CSV",
            last_name="Worker",
            email="csv.worker@test.local",
        )
        job = Job.objects.create(
            client=client,
            selected_provider=provider,
            hold_worker=worker,
            service_type=service_type,
            provider_service_name_snapshot="CSV Offer",
            requested_total_snapshot=Decimal("120.00"),
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="13 Job St",
        )
        PlatformLedgerEntry.objects.create(
            job=job,
            gross_cents=12_000,
            fee_cents=2_500,
            net_provider_cents=9_500,
            is_final=True,
        )

        response = export_activity_csv(
            actor_type="client",
            actor=client,
            params={},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("activity_export.csv", response["Content-Disposition"])
        content = response.content.decode("utf-8")
        self.assertIn(
            "Job ID,Date,Service,Provider,Status,Total charged,Cancelled Reason",
            content,
        )
        self.assertIn("CSV Service", content)
        self.assertIn("CSV Provider", content)
        self.assertIn("120.00", content)
        self.assertIn("Disclaimer", content)
        self.assertIn("informational and operational purposes only", content)
        self.assertNotIn("CSV Client", content)
        self.assertNotIn("CSV Worker", content)

    def test_export_activity_csv_applies_filters(self):
        client = Client.objects.create(
            first_name="Filtered",
            last_name="CSV",
            email="filtered.csv.client@test.local",
            phone_number="+15145551113",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="14 Client St",
        )
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Filtered CSV Provider",
            contact_first_name="Filtered",
            contact_last_name="Provider",
            phone_number="+15145551114",
            email="filtered.csv.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="15 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Filtered CSV Service",
            description="Filtered CSV Service",
        )
        recent_completed_job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Recent Completed CSV Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="16 Job St",
        )
        old_completed_job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Old Completed CSV Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="17 Job St",
        )
        Job.objects.filter(pk=recent_completed_job.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )
        Job.objects.filter(pk=old_completed_job.pk).update(
            created_at=timezone.now() - timedelta(days=40)
        )

        response = export_activity_csv(
            actor_type="client",
            actor=client,
            params={
                "status": "completed",
                "range": "30d",
                "sort": "oldest",
            },
        )

        content = response.content.decode("utf-8")
        self.assertIn("Total charged", content)
        self.assertIn(str(recent_completed_job.job_id), content)
        self.assertNotIn(str(old_completed_job.job_id), content)

    def test_export_activity_csv_includes_cancel_reason(self):
        client = Client.objects.create(
            first_name="Cancelled",
            last_name="Client",
            email="cancelled.csv.client@test.local",
            phone_number="+15145551115",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="18 Client St",
        )
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Cancelled CSV Provider",
            contact_first_name="Cancelled",
            contact_last_name="Provider",
            phone_number="+15145551116",
            email="cancelled.csv.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="19 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Cancelled CSV Service",
            description="Cancelled CSV Service",
        )
        Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Cancelled CSV Offer",
            requested_total_snapshot=Decimal("80.00"),
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.CANCELLED,
            cancelled_by=Job.CancellationActor.CLIENT,
            cancel_reason=Job.CancelReason.CLIENT_CANCELLED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="20 Job St",
        )

        response = export_activity_csv(
            actor_type="client",
            actor=client,
            params={},
        )

        content = response.content.decode("utf-8")
        self.assertIn("Client cancelled", content)

    def test_export_activity_csv_uses_assignment_provider_when_selected_provider_is_cleared(self):
        client = Client.objects.create(
            first_name="Assigned",
            last_name="Client",
            email="assigned.csv.client@test.local",
            phone_number="+15145551117",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="21 Client St",
        )
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Assigned CSV Provider",
            contact_first_name="Assigned",
            contact_last_name="Provider",
            phone_number="+15145551118",
            email="assigned.csv.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="22 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Assigned CSV Service",
            description="Assigned CSV Service",
        )
        job = Job.objects.create(
            client=client,
            service_type=service_type,
            provider_service_name_snapshot="Assigned CSV Offer",
            requested_total_snapshot=Decimal("95.00"),
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.ASSIGNED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="23 Job St",
        )
        JobAssignment.objects.create(
            job=job,
            provider=provider,
            is_active=True,
        )

        response = export_activity_csv(
            actor_type="client",
            actor=client,
            params={},
        )

        content = response.content.decode("utf-8")
        self.assertIn(str(job.job_id), content)
        self.assertIn("Assigned Provider", content)
