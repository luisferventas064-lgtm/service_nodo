from django.test import TestCase
from django.urls import reverse

from providers.models import Provider, ProviderService, ServiceCategory


class ProviderServiceManagementFlowTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            company_name=None,
            legal_name="Jane Smith",
            contact_first_name="Jane",
            contact_last_name="Smith",
            phone_number="+15145550300",
            email="services.provider@example.com",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area="Montreal",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="123 Provider St",
        )
        self.category = ServiceCategory.objects.create(
            name="Painting",
            slug="painting",
        )
        session = self.client.session
        session["provider_id"] = self.provider.pk
        session.save()

    def test_provider_service_add_creates_service(self):
        response = self.client.post(
            reverse("provider_service_add"),
            data={
                "category": self.category.pk,
                "custom_name": "Interior Painting",
                "billing_unit": "fixed",
                "price_cents": 25000,
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("provider_services_list"))
        self.assertTrue(
            ProviderService.objects.filter(
                provider=self.provider,
                custom_name="Interior Painting",
                is_active=True,
            ).exists()
        )
        self.provider.refresh_from_db()
        self.assertTrue(self.provider.is_operational)

    def test_provider_service_edit_updates_service(self):
        service = ProviderService.objects.create(
            provider=self.provider,
            category=self.category,
            custom_name="Interior Painting",
            description="",
            billing_unit="fixed",
            price_cents=25000,
            is_active=True,
        )

        response = self.client.post(
            reverse("provider_service_edit", args=[service.pk]),
            data={
                "category": self.category.pk,
                "custom_name": "Exterior Painting",
                "billing_unit": "hour",
                "price_cents": 30000,
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("provider_services_list"))
        service.refresh_from_db()
        self.assertEqual(service.custom_name, "Exterior Painting")
        self.assertEqual(service.billing_unit, "hour")
        self.assertEqual(service.price_cents, 30000)

    def test_provider_service_toggle_flips_active_state(self):
        service = ProviderService.objects.create(
            provider=self.provider,
            category=self.category,
            custom_name="Interior Painting",
            description="",
            billing_unit="fixed",
            price_cents=25000,
            is_active=True,
        )

        response = self.client.post(
            reverse("provider_service_toggle", args=[service.pk]),
        )

        self.assertRedirects(response, reverse("provider_services_list"))
        service.refresh_from_db()
        self.assertFalse(service.is_active)

    def test_provider_services_list_shows_existing_services(self):
        ProviderService.objects.create(
            provider=self.provider,
            category=self.category,
            custom_name="Interior Painting",
            description="",
            billing_unit="fixed",
            price_cents=25000,
            is_active=True,
        )

        response = self.client.get(reverse("provider_services_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Services")
        self.assertContains(response, "Interior Painting")

    def test_unverified_provider_cannot_manage_services(self):
        self.provider.is_phone_verified = False
        self.provider.save(update_fields=["is_phone_verified"])

        response = self.client.get(reverse("provider_services_list"))

        self.assertRedirects(
            response,
            reverse("provider_complete_profile"),
            fetch_redirect_response=False,
        )

    def test_incomplete_profile_provider_cannot_manage_services(self):
        self.provider.profile_completed = False
        self.provider.save(update_fields=["profile_completed"])

        response = self.client.get(reverse("provider_services_list"))

        self.assertRedirects(response, reverse("provider_complete_profile"))

    def test_billing_incomplete_provider_can_still_manage_services(self):
        self.provider.billing_profile_completed = False
        self.provider.save(update_fields=["billing_profile_completed"])

        response = self.client.get(reverse("provider_services_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Services")
