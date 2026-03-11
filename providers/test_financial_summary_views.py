from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from clients.models import Client
from jobs.models import Job, PlatformLedgerEntry
from providers.models import Provider
from service_type.models import ServiceType


class ProviderFinancialSummaryViewTests(TestCase):
    def _login_provider(self, provider):
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

    def test_provider_financial_summary_renders_provider_metrics_and_monthly_breakdown(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Finance Provider",
            contact_first_name="Finance",
            contact_last_name="Provider",
            phone_number="+15145551201",
            email="finance.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1 Finance St",
        )
        client = Client.objects.create(
            first_name="Finance",
            last_name="Client",
            email="finance.client@test.local",
            phone_number="+15145551202",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="2 Finance St",
        )
        service_type = ServiceType.objects.create(
            name="Financial Summary Service",
            description="Financial Summary Service",
        )
        january_job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="January Offer",
            requested_total_snapshot=Decimal("120.00"),
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="3 Finance St",
        )
        february_job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="February Offer",
            requested_total_snapshot=Decimal("80.00"),
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.CANCELLED,
            cancelled_by=Job.CancellationActor.CLIENT,
            cancel_reason=Job.CancelReason.CLIENT_CANCELLED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="4 Finance St",
        )
        Job.objects.filter(pk=january_job.pk).update(
            created_at=datetime(2026, 1, 15, 12, 0, tzinfo=dt_timezone.utc)
        )
        Job.objects.filter(pk=february_job.pk).update(
            created_at=datetime(2026, 2, 20, 12, 0, tzinfo=dt_timezone.utc)
        )

        PlatformLedgerEntry.objects.create(
            job=january_job,
            gross_cents=12_000,
            fee_cents=2_500,
            net_provider_cents=9_500,
            is_final=True,
        )

        other_provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Other Finance Provider",
            contact_first_name="Other",
            contact_last_name="Provider",
            phone_number="+15145551203",
            email="other.finance.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A3",
            address_line1="5 Finance St",
        )
        Job.objects.create(
            client=client,
            selected_provider=other_provider,
            service_type=service_type,
            provider_service_name_snapshot="Other Provider Offer",
            requested_total_snapshot=Decimal("300.00"),
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A3",
            address_line1="6 Finance St",
        )

        self._login_provider(provider)

        response = self.client.get(reverse("provider_financial_summary"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Financial Summary")
        self.assertContains(response, "Provider net")
        self.assertContains(response, "Gross")
        self.assertContains(response, "Platform fees")
        self.assertContains(response, "Monthly breakdown")
        self.assertContains(response, "Financial Reporting and Tax Responsibility")
        self.assertContains(response, "informational and operational purposes only")
        self.assertContains(response, "professional accounting, tax, or legal advice")
        self.assertContains(response, "2026-01")
        self.assertContains(response, "2026-02")
        self.assertContains(response, "120.00")
        self.assertContains(response, "95.00")
        self.assertContains(response, "25.00")
        self.assertEqual(response.context["activity_analytics"]["total_jobs"], 2)
        self.assertEqual(response.context["activity_analytics"]["cancelled_jobs"], 1)
        self.assertEqual(len(response.context["monthly_revenue"]), 2)
        self.assertEqual(response.context["page_title"], "Financial Summary")
        self.assertEqual(response.context["role"], "provider")
        self.assertFalse(response.context["show_activity_table"])

    def test_provider_financial_summary_uses_provider_context_flags(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Context Provider",
            contact_first_name="Context",
            contact_last_name="Provider",
            phone_number="+15145551204",
            email="context.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A4",
            address_line1="7 Finance St",
        )
        self._login_provider(provider)

        response = self.client.get(reverse("provider_financial_summary"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["role"], "provider")
        self.assertFalse(response.context["show_activity_table"])
        self.assertIn("activity_analytics", response.context)
        self.assertIn("monthly_revenue", response.context)

    def test_provider_financial_summary_exports_csv(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Export Financial Provider",
            contact_first_name="Export",
            contact_last_name="Financial",
            phone_number="+15145551211",
            email="export.financial.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="11 Finance St",
        )
        client = Client.objects.create(
            first_name="Export",
            last_name="Financial Client",
            email="export.financial.client@test.local",
            phone_number="+15145551212",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="12 Finance St",
        )
        service_type = ServiceType.objects.create(
            name="Export Financial Service",
            description="Export Financial Service",
        )
        job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Export Financial Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="13 Finance St",
        )

        self._login_provider(provider)

        response = self.client.get(reverse("provider_financial_summary"), {"export": "csv"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode("utf-8")
        self.assertIn(
            "Job ID,Date,Service,Worker,Status,Gross,Platform fee,Provider net,Cancelled Reason",
            content,
        )
        self.assertIn(str(job.job_id), content)
        self.assertIn("Export Financial", content)
        self.assertIn("informational and operational purposes only", content)
        self.assertNotIn("Export Financial Client", content)
