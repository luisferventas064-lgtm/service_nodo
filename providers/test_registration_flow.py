from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from providers.models import Provider, ProviderService, ServiceCategory


class ProviderRegistrationFlowTests(TestCase):
    def test_provider_register_creates_pending_provider_and_redirects_to_verify(self):
        response = self.client.post(
            reverse("provider_register"),
            data={
                "business_name": "Acme Services",
                "email": "acme.services@example.com",
                "phone_number": "+15145550200",
                "provider_type": "company",
            },
        )

        self.assertRedirects(response, reverse("verify_phone"))
        provider = Provider.objects.get(email="acme.services@example.com")
        self.assertEqual(provider.provider_type, Provider.TYPE_COMPANY)
        self.assertFalse(provider.is_phone_verified)
        self.assertFalse(provider.profile_completed)
        self.assertFalse(provider.billing_profile_completed)
        self.assertFalse(provider.accepts_terms)
        self.assertEqual(self.client.session["verify_actor_type"], "provider")
        self.assertEqual(self.client.session["verify_actor_id"], provider.pk)

    @patch("verifications.services.send_sms")
    def test_verify_phone_redirects_provider_to_complete_profile(self, send_sms_mock):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            contact_first_name="Pending",
            contact_last_name="Provider",
            phone_number="+15145550201",
            email="pending.provider@example.com",
            profile_completed=False,
            billing_profile_completed=False,
            accepts_terms=False,
            province="QC",
            city="Pending",
            postal_code="PENDING",
            address_line1="Pending profile completion",
        )

        from verifications.services import create_phone_verification

        code, _ = create_phone_verification(
            actor_type="provider",
            actor_id=provider.pk,
            phone_number=provider.phone_number,
        )

        session = self.client.session
        session["verify_actor_type"] = "provider"
        session["verify_actor_id"] = provider.pk
        session.save()

        response = self.client.post(reverse("verify_phone"), data={"code": code})

        self.assertRedirects(response, reverse("provider_complete_profile"))
        provider.refresh_from_db()
        self.assertTrue(provider.is_phone_verified)
        self.assertEqual(self.client.session["provider_id"], provider.pk)

    def test_provider_complete_profile_redirects_to_dashboard(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Acme Services",
            contact_first_name="Pending",
            contact_last_name="Contact",
            phone_number="+15145550202",
            email="complete.provider@example.com",
            is_phone_verified=True,
            profile_completed=False,
            billing_profile_completed=False,
            accepts_terms=False,
            province="QC",
            city="Pending",
            postal_code="PENDING",
            address_line1="Pending profile completion",
        )

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.post(
            reverse("provider_complete_profile"),
            data={
                "business_registration_number": "REG-123",
                "contact_person_name": "Jane Manager",
                "service_area": "Montreal North Shore",
                "accepts_terms": "on",
            },
        )

        self.assertRedirects(response, reverse("provider_complete_billing"))
        provider.refresh_from_db()
        self.assertTrue(provider.profile_completed)
        self.assertFalse(provider.billing_profile_completed)
        self.assertTrue(provider.accepts_terms)
        self.assertEqual(provider.contact_first_name, "Jane")
        self.assertEqual(provider.contact_last_name, "Manager")

    def test_provider_complete_billing_marks_billing_complete_and_redirects_dashboard(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Acme Services",
            contact_first_name="Jane",
            contact_last_name="Manager",
            phone_number="+15145550205",
            email="billing.provider@example.com",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=False,
            accepts_terms=True,
            province="QC",
            city="Pending",
            postal_code="PENDING",
            address_line1="Pending profile completion",
        )

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.post(
            reverse("provider_complete_billing"),
            data={
                "province": "QC",
                "city": "Montreal",
                "postal_code": "H1A1A1",
                "address_line1": "200 Business Ave",
            },
        )

        self.assertRedirects(response, reverse("provider_dashboard"))
        provider.refresh_from_db()
        self.assertTrue(provider.billing_profile_completed)
        self.assertEqual(provider.city, "Montreal")

    def test_provider_is_operational_requires_active_service(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            company_name=None,
            legal_name="Jane Smith",
            contact_first_name="Jane",
            contact_last_name="Smith",
            phone_number="+15145550203",
            email="operational.provider@example.com",
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

        self.assertFalse(provider.is_operational)

        category = ServiceCategory.objects.create(name="Cleaning", slug="cleaning")
        ProviderService.objects.create(
            provider=provider,
            category=category,
            custom_name="Deep Cleaning",
            description="",
            billing_unit="hour",
            price_cents=10000,
            is_active=True,
        )

        self.assertTrue(provider.has_active_service())
        self.assertTrue(provider.is_operational)

    def test_provider_dashboard_shows_activation_banner_until_service_exists(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            company_name=None,
            legal_name="Jane Smith",
            contact_first_name="Jane",
            contact_last_name="Smith",
            phone_number="+15145550204",
            email="dashboard.provider@example.com",
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
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("provider_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Setup in progress.")
        self.assertContains(response, "Add at least one active service.")
        self.assertContains(response, "Active Services: 0")

    def test_provider_dashboard_shows_billing_banner_when_billing_missing(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            company_name=None,
            legal_name="Jane Smith",
            contact_first_name="Jane",
            contact_last_name="Smith",
            phone_number="+15145550206",
            email="billing.banner.provider@example.com",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=False,
            accepts_terms=True,
            service_area="Montreal",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="123 Provider St",
        )
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("provider_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Complete your billing information.")
        self.assertContains(response, "Add at least one active service.")

    def test_provider_dashboard_shows_operational_state_when_ready(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            company_name=None,
            legal_name="Ready Provider",
            contact_first_name="Ready",
            contact_last_name="Provider",
            phone_number="+15145550207",
            email="ready.provider@example.com",
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
        category = ServiceCategory.objects.create(name="Plumbing", slug="plumbing")
        ProviderService.objects.create(
            provider=provider,
            category=category,
            custom_name="Leak Repair",
            description="",
            billing_unit="fixed",
            price_cents=18000,
            is_active=True,
        )
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("provider_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your account is operational.")
        self.assertContains(response, "Active Services: 1")
