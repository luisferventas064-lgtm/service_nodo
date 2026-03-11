from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Client
from jobs.models import Job
from providers.models import Provider
from service_type.models import ServiceType


class ProviderActivityViewTests(TestCase):
    def test_provider_activity_uses_shared_activity_context(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Active Provider",
            contact_first_name="Active",
            contact_last_name="Provider",
            phone_number="+15145551001",
            email="active.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1 Provider St",
        )
        client = Client.objects.create(
            first_name="Activity",
            last_name="Client",
            email="activity.client@test.local",
            phone_number="+15145551002",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="2 Client St",
        )
        service_type = ServiceType.objects.create(
            name="Provider Activity Service",
            description="Provider Activity Service",
        )
        matching_job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Provider Activity Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="3 Job St",
        )
        other_provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Other Provider",
            contact_first_name="Other",
            contact_last_name="Provider",
            phone_number="+15145551003",
            email="other.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="4 Provider St",
        )
        Job.objects.create(
            client=client,
            selected_provider=other_provider,
            service_type=service_type,
            provider_service_name_snapshot="Other Provider Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.ASSIGNED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A3",
            address_line1="5 Job St",
        )

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("provider_activity"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity History")
        self.assertContains(response, matching_job.public_reference)
        self.assertContains(response, "Activity Client")
        self.assertContains(response, "Client")
        self.assertContains(response, "All (1)")
        self.assertContains(response, "Posted (1)")
        self.assertContains(response, "Total jobs")
        self.assertContains(response, "Gross")
        self.assertContains(response, "Provider net")
        self.assertContains(response, "Platform fees")
        self.assertContains(response, "informational and operational purposes only")
        self.assertNotContains(response, "Payment")
        self.assertNotContains(response, "Other Provider Offer")

    def test_provider_activity_supports_second_page(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Paged Provider",
            contact_first_name="Paged",
            contact_last_name="Provider",
            phone_number="+15145551011",
            email="paged.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="11 Provider St",
        )
        client = Client.objects.create(
            first_name="Paged",
            last_name="Client",
            email="paged.activity.client@test.local",
            phone_number="+15145551012",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="12 Client St",
        )
        service_type = ServiceType.objects.create(
            name="Paged Provider Activity Service",
            description="Paged Provider Activity Service",
        )
        for index in range(11):
            Job.objects.create(
                client=client,
                selected_provider=provider,
                service_type=service_type,
                provider_service_name_snapshot=f"Provider Page Offer {index}",
                job_mode=Job.JobMode.ON_DEMAND,
                job_status=Job.JobStatus.POSTED,
                is_asap=True,
                country="Canada",
                province="QC",
                city="Montreal",
                postal_code="H1A1A1",
                address_line1="13 Job St",
            )

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("provider_activity"), {"page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertTrue(response.context["is_paginated"])
        self.assertContains(response, "Page 2 of 2")

    def test_provider_activity_supports_date_range_filter(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Range Provider",
            contact_first_name="Range",
            contact_last_name="Provider",
            phone_number="+15145551021",
            email="range.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="21 Provider St",
        )
        client = Client.objects.create(
            first_name="Range",
            last_name="Client",
            email="range.activity.client@test.local",
            phone_number="+15145551022",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="22 Client St",
        )
        service_type = ServiceType.objects.create(
            name="Range Provider Activity Service",
            description="Range Provider Activity Service",
        )
        recent_job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Recent Provider Range Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="23 Job St",
        )
        old_job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Old Provider Range Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="24 Job St",
        )
        Job.objects.filter(pk=recent_job.pk).update(created_at=timezone.now() - timedelta(days=2))
        Job.objects.filter(pk=old_job.pk).update(created_at=timezone.now() - timedelta(days=8))

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("provider_activity"), {"range": "7d"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_range"], "7d")
        self.assertContains(response, "Recent Provider Range Offer")
        self.assertNotContains(response, "Old Provider Range Offer")

    def test_provider_activity_exports_csv(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Export Provider",
            contact_first_name="Export",
            contact_last_name="Provider",
            phone_number="+15145551031",
            email="export.activity.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="31 Provider St",
        )
        client = Client.objects.create(
            first_name="Export",
            last_name="Client",
            email="export.activity.client@test.local",
            phone_number="+15145551032",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="32 Client St",
        )
        service_type = ServiceType.objects.create(
            name="Export Provider Activity Service",
            description="Export Provider Activity Service",
        )
        job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Provider Export Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="33 Job St",
        )

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("provider_activity"), {"export": "csv"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode("utf-8")
        self.assertIn(
            "Job ID,Date,Service,Worker,Status,Gross,Platform fee,Provider net,Cancelled Reason",
            content,
        )
        self.assertIn(str(job.job_id), content)
        self.assertIn("Export", content)
        self.assertIn("informational and operational purposes only", content)
        self.assertNotIn("Export Client", content)
