from django.test import TestCase
from django.urls import reverse

from clients.models import Client
from providers.models import Provider, ProviderService
from service_type.models import ServiceType
from workers.models import Worker


class PortalRoutingTests(TestCase):
    def test_client_dashboard_alias_redirects_to_client_dashboard(self):
        client_obj = Client.objects.create(
            first_name="Portal",
            last_name="Client",
            phone_number="5550001020",
            email="portal.dashboard.client@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1020 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("portal:client_dashboard"))

        self.assertRedirects(response, reverse("client_dashboard"))

    def test_client_dashboard_alias_redirects_to_login_for_non_client(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            contact_first_name="Portal",
            contact_last_name="Provider",
            phone_number="5550001021",
            email="portal.dashboard.provider@test.local",
            profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1021 Provider St",
        )
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("portal:client_dashboard"))

        self.assertRedirects(
            response,
            reverse("ui:root_login"),
            fetch_redirect_response=False,
        )

    def test_provider_dashboard_renders_portal_dashboard(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            contact_first_name="Portal",
            contact_last_name="Provider",
            phone_number="5550001022",
            email="portal.dashboard.provider2@test.local",
            profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1022 Provider St",
        )
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("portal:provider_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provider Dashboard")

    def test_worker_dashboard_alias_redirects_to_worker_jobs(self):
        worker = Worker.objects.create(
            first_name="Portal",
            last_name="Worker",
            phone_number="5550001023",
            email="portal.dashboard.worker@test.local",
            profile_completed=True,
            is_phone_verified=True,
            accepts_terms=True,
        )
        session = self.client.session
        session["worker_id"] = worker.pk
        session.save()

        response = self.client.get(reverse("portal:worker_dashboard"))

        self.assertRedirects(response, reverse("worker_jobs"))


class ProviderServicesPortalTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Portal Services Provider",
            contact_first_name="Portal",
            contact_last_name="Services",
            phone_number="+15145550999",
            email="portal.services.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area="Montreal",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="999 Provider St",
        )
        self.service_type = ServiceType.objects.create(
            name="Window Cleaning",
            description="Window cleaning",
            is_active=True,
        )
        session = self.client.session
        session["provider_id"] = self.provider.pk
        session.save()

    def test_provider_services_view_renders(self):
        response = self.client.get(reverse("portal:provider_services"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Services")

    def test_provider_services_view_redirects_when_profile_incomplete(self):
        self.provider.legal_name = ""
        self.provider.profile_completed = False
        self.provider.save(update_fields=["legal_name", "profile_completed"])

        response = self.client.get(reverse("portal:provider_services"))

        self.assertRedirects(
            response,
            reverse("provider_complete_profile"),
            fetch_redirect_response=False,
        )

    def test_provider_service_add_view_creates_service(self):
        response = self.client.post(
            reverse("portal:provider_service_add", args=[self.service_type.service_type_id]),
            data={
                "custom_name": "Window Shine",
                "description": "Exterior window cleaning",
                "billing_unit": "fixed",
                "price": "120.50",
            },
        )

        self.assertRedirects(response, reverse("portal:provider_services"))
        service = ProviderService.objects.get(provider=self.provider, service_type=self.service_type)
        self.assertEqual(service.price_cents, 12050)
        self.assertTrue(service.is_active)
