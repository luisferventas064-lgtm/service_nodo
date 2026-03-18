from django.test import TestCase
from django.urls import reverse

from compliance.models import ComplianceRule
from providers.models import Provider, ProviderCertificate, ProviderService, ProviderServiceArea
from service_type.models import ServiceType


class EnglishLocaleTestMixin:
    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_ACCEPT_LANGUAGE"] = "en"


class ProviderServiceManagementFlowTests(EnglishLocaleTestMixin, TestCase):
    def setUp(self):
        super().setUp()
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
        self.service_type = ServiceType.objects.create(
            name="Painting",
            description="Painting",
        )
        ProviderServiceArea.objects.create(
            provider=self.provider,
            city="Montreal",
            province="QC",
            is_active=True,
        )
        session = self.client.session
        session["provider_id"] = self.provider.pk
        session.save()

    def test_provider_service_add_creates_service(self):
        response = self.client.post(
            reverse("portal:provider_service_add", args=[self.service_type.service_type_id]),
            data={
                "custom_name": "Interior Painting",
                "description": "",
                "billing_unit": "fixed",
                "price": "250.00",
            },
        )

        self.assertRedirects(response, reverse("portal:provider_services"))
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
            service_type=self.service_type,
            custom_name="Interior Painting",
            description="",
            billing_unit="fixed",
            price_cents=25000,
            is_active=True,
        )

        response = self.client.post(
            reverse("portal:provider_service_edit", args=[service.pk]),
            data={
                "custom_name": "Exterior Painting",
                "description": "",
                "billing_unit": "hour",
                "price": "300.00",
            },
        )

        self.assertRedirects(response, reverse("portal:provider_services"))
        service.refresh_from_db()
        self.assertEqual(service.custom_name, "Exterior Painting")
        self.assertEqual(service.billing_unit, "hour")
        self.assertEqual(service.price_cents, 30000)

    def test_provider_service_toggle_flips_active_state(self):
        service = ProviderService.objects.create(
            provider=self.provider,
            service_type=self.service_type,
            custom_name="Interior Painting",
            description="",
            billing_unit="fixed",
            price_cents=25000,
            is_active=True,
        )

        response = self.client.post(
            reverse("portal:provider_service_toggle", args=[service.pk]),
        )

        self.assertRedirects(response, reverse("portal:provider_services"))
        service.refresh_from_db()
        self.assertFalse(service.is_active)

    def test_provider_services_list_shows_existing_services(self):
        ProviderService.objects.create(
            provider=self.provider,
            service_type=self.service_type,
            custom_name="Interior Painting",
            description="",
            billing_unit="fixed",
            price_cents=25000,
            is_active=True,
        )

        response = self.client.get(reverse("portal:provider_services"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Services")
        self.assertContains(response, "Interior Painting")

    def test_provider_services_list_shows_missing_certificate_warning(self):
        ProviderService.objects.create(
            provider=self.provider,
            service_type=self.service_type,
            custom_name="Interior Painting",
            description="",
            billing_unit="fixed",
            price_cents=25000,
            is_active=True,
        )
        ComplianceRule.objects.create(
            province_code="QC",
            service_type=self.service_type,
            certificate_name="RBQ License",
            certificate_required=True,
            insurance_required=False,
            is_mandatory=True,
        )

        response = self.client.get(reverse("portal:provider_services"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Missing certificates")
        self.assertContains(response, "RBQ License")

    def test_provider_service_add_shows_insurance_required_message(self):
        ComplianceRule.objects.create(
            province_code="QC",
            service_type=self.service_type,
            certificate_name="",
            certificate_required=False,
            insurance_required=True,
            is_mandatory=True,
        )

        response = self.client.get(
            reverse("portal:provider_service_add", args=[self.service_type.service_type_id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Insurance is required for this service and province.")

    def test_provider_service_edit_shows_compliant_status_when_certificate_is_verified(self):
        service = ProviderService.objects.create(
            provider=self.provider,
            service_type=self.service_type,
            custom_name="Interior Painting",
            description="",
            billing_unit="fixed",
            price_cents=25000,
            is_active=True,
        )
        ComplianceRule.objects.create(
            province_code="QC",
            service_type=self.service_type,
            certificate_name="RBQ License",
            certificate_required=True,
            insurance_required=False,
            is_mandatory=True,
        )
        ProviderCertificate.objects.create(
            provider=self.provider,
            cert_type="RBQ License",
            status=ProviderCertificate.Status.VERIFIED,
        )

        response = self.client.get(reverse("portal:provider_service_edit", args=[service.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compliant for Painting in QC.")

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
        self.provider.accepts_terms = False
        self.provider.save(update_fields=["accepts_terms"])

        response = self.client.get(reverse("provider_services_list"), follow=True)

        self.assertRedirects(response, reverse("provider_complete_profile"))

    def test_billing_incomplete_provider_can_still_manage_services(self):
        self.provider.billing_profile_completed = False
        self.provider.save(update_fields=["billing_profile_completed"])

        response = self.client.get(reverse("portal:provider_services"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Services")
