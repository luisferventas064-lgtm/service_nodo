from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from clients.models import Client
from providers.models import Provider
from service_type.models import ServiceType


class QualityProvidersDashboardViewTests(TestCase):
    def test_staff_can_load_quality_dashboard(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="quality_dashboard_staff",
            password="test-pass-123",
            is_staff=True,
        )
        self.client.force_login(user)

        response = self.client.get("/admin/quality/providers/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provider Quality Dashboard")


class HomeViewTests(TestCase):
    def test_home_shows_navigation_links(self):
        response = self.client.get(reverse("ui:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NODO - Test Interface")
        self.assertContains(response, "Register Client")
        self.assertContains(response, "Register Provider")
        self.assertContains(response, "View Marketplace")


class ProfileViewsTests(TestCase):
    def test_client_profile_is_visible_from_session(self):
        client_obj = Client.objects.create(
            first_name="Client",
            last_name="Visible",
            phone_number="5550000100",
            email="client.visible@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="10 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("client_profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Client Profile")
        self.assertContains(response, "Client Visible")
        self.assertContains(response, "client.visible@test.local")

    def test_provider_profile_is_visible_from_session(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            legal_name="Provider Visible",
            contact_first_name="Provider",
            contact_last_name="Visible",
            phone_number="5550000101",
            email="provider.visible@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=False,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="11 Provider St",
        )
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("provider_profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provider Profile")
        self.assertContains(response, "self_employed")
        self.assertContains(response, "Operational")


class RequestCreateViewTests(TestCase):
    def test_unverified_client_cannot_create_job(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Request",
            phone_number="5550000001",
            email="provider.request@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="10 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Request Create Test",
            description="Request Create Test",
        )
        Client.objects.create(
            first_name="Client",
            last_name="Request",
            phone_number="5550000002",
            email="client.request@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="11 Client St",
            is_phone_verified=False,
        )

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "first_name": "Client",
                "last_name": "Request",
                "phone_number": "5550000002",
                "email": "client.request@test.local",
                "country": "Canada",
                "province": "QC",
                "city": "Laval",
                "postal_code": "H7A0A1",
                "address_line1": "11 Client St",
                "service_type": str(service_type.pk),
                "job_mode": "on_demand",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"PHONE_NOT_VERIFIED", response.content)

    def test_incomplete_profile_client_is_redirected_with_warning(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Profile",
            phone_number="5550000011",
            email="provider.profile@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="20 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Profile Gate Test",
            description="Profile Gate Test",
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Profile",
            phone_number="5550000012",
            email="client.profile@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="21 Client St",
            is_phone_verified=True,
            profile_completed=False,
        )

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "first_name": "Client",
                "last_name": "Profile",
                "phone_number": "5550000012",
                "email": "client.profile@test.local",
                "country": "Canada",
                "province": "QC",
                "city": "Laval",
                "postal_code": "H7A0A1",
                "address_line1": "21 Client St",
                "service_type": str(service_type.pk),
                "job_mode": "on_demand",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("client_complete_profile"))
        self.assertContains(
            response,
            "You must complete your profile before creating a job.",
        )
        self.assertEqual(self.client.session["client_id"], client.pk)
