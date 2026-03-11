from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from providers.models import (
    Provider,
    ProviderCertificate,
    ProviderLocation,
    ProviderService,
    ProviderServiceArea,
)
from service_type.models import RequiredCertification, ServiceType
from ui.models import PasswordResetCode


class ProviderRegistrationFlowTests(TestCase):
    def setUp(self):
        super().setUp()
        self.geocode_address_patcher = patch("providers.views.geocode_address", return_value=None)
        self.geocode_address_mock = self.geocode_address_patcher.start()
        self.addCleanup(self.geocode_address_patcher.stop)

    @patch("providers.views.send_sms")
    def test_provider_register_creates_pending_provider_and_redirects_to_verify(self, send_sms_mock):
        response = self.client.post(
            reverse("provider_register"),
            data={
                "business_name": "Acme Services",
                "email": "acme.services@example.com",
                "country": "CA",
                "phone_local": "4388365524",
                "password": "test-pass-123",
                "confirm_password": "test-pass-123",
                "provider_type": "company",
            },
        )

        self.assertRedirects(response, reverse("verify_phone"))
        provider = Provider.objects.get(email="acme.services@example.com")
        self.assertEqual(provider.phone_number, "+14388365524")
        self.assertEqual(provider.provider_type, Provider.TYPE_COMPANY)
        self.assertFalse(provider.is_phone_verified)
        self.assertFalse(provider.profile_completed)
        self.assertFalse(provider.billing_profile_completed)
        self.assertFalse(provider.accepts_terms)
        self.assertEqual(self.client.session["verify_phone"], provider.phone_number)
        self.assertEqual(self.client.session["verify_role"], "provider")
        self.assertEqual(self.client.session["verify_actor_type"], "provider")
        self.assertEqual(self.client.session["verify_actor_id"], provider.pk)
        self.assertTrue(
            PasswordResetCode.objects.filter(
                phone_number=provider.phone_number,
                purpose="verify",
            ).exists()
        )
        send_sms_mock.assert_called_once()

    def test_provider_register_rejects_duplicate_email(self):
        Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Existing Provider",
            contact_first_name="Existing",
            contact_last_name="Provider",
            phone_number="+14388365529",
            email="acme.services@example.com",
            profile_completed=False,
            billing_profile_completed=False,
            accepts_terms=False,
            province="QC",
            city="Pending",
            postal_code="PENDING",
            address_line1="Pending profile completion",
        )

        response = self.client.post(
            reverse("provider_register"),
            data={
                "business_name": "Acme Services",
                "email": "ACME.SERVICES@example.com",
                "country": "CA",
                "phone_local": "4388365524",
                "password": "test-pass-123",
                "confirm_password": "test-pass-123",
                "provider_type": "company",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A provider with this email already exists.")
        self.assertEqual(Provider.objects.filter(email__iexact="acme.services@example.com").count(), 1)

    def test_provider_register_rejects_duplicate_phone_number(self):
        Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Existing Provider",
            contact_first_name="Existing",
            contact_last_name="Provider",
            phone_number="+14388365524",
            email="existing-provider@example.com",
            profile_completed=False,
            billing_profile_completed=False,
            accepts_terms=False,
            province="QC",
            city="Pending",
            postal_code="PENDING",
            address_line1="Pending profile completion",
        )

        response = self.client.post(
            reverse("provider_register"),
            data={
                "business_name": "Acme Services",
                "email": "new-provider@example.com",
                "country": "CA",
                "phone_local": "4388365524",
                "password": "test-pass-123",
                "confirm_password": "test-pass-123",
                "provider_type": "company",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A provider with this phone number already exists.")
        self.assertEqual(Provider.objects.filter(phone_number="+14388365524").count(), 1)

    def test_provider_register_rejects_password_mismatch(self):
        response = self.client.post(
            reverse("provider_register"),
            data={
                "business_name": "Acme Services",
                "email": "acme.mismatch@example.com",
                "country": "CA",
                "phone_local": "4388365599",
                "password": "test-pass-123",
                "confirm_password": "test-pass-124",
                "provider_type": "company",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "There was a problem with your submission.")
        self.assertContains(response, "Passwords do not match.")
        self.assertFalse(Provider.objects.filter(email="acme.mismatch@example.com").exists())

    def test_verify_phone_redirects_provider_to_portal_router(self):
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
        PasswordResetCode.objects.create(
            phone_number=provider.phone_number,
            code="123456",
            purpose="verify",
        )

        session = self.client.session
        session["verify_phone"] = provider.phone_number
        session["verify_role"] = "provider"
        session.save()

        response = self.client.post(reverse("verify_phone"), data={"code": "123456"})

        self.assertRedirects(
            response,
            reverse("ui:portal"),
            fetch_redirect_response=False,
        )
        provider.refresh_from_db()
        self.assertTrue(provider.is_phone_verified)
        self.assertEqual(self.client.session["provider_id"], provider.pk)
        record = PasswordResetCode.objects.get(
            phone_number=provider.phone_number,
            purpose="verify",
        )
        self.assertTrue(record.used)

    def test_provider_complete_profile_redirects_to_dashboard(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Acme Services",
            contact_first_name="Jane",
            contact_last_name="Manager",
            business_registration_number="REG-123",
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
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Montreal",
            province="QC",
            is_active=True,
        )

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.post(
            reverse("provider_complete_profile"),
            data={
                "action": "accept_terms",
            },
        )

        self.assertRedirects(response, reverse("portal:provider_dashboard"))
        provider.refresh_from_db()
        self.assertTrue(provider.profile_completed)
        self.assertFalse(provider.billing_profile_completed)
        self.assertTrue(provider.accepts_terms)
        self.assertEqual(provider.contact_first_name, "Jane")
        self.assertEqual(provider.contact_last_name, "Manager")
        self.assertEqual(self.client.session.get("nodo_role"), "provider")
        self.assertEqual(self.client.session.get("nodo_profile_id"), provider.pk)

    def test_provider_complete_profile_keeps_incomplete_individual_provider_false(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            company_name=None,
            legal_name="",
            contact_first_name="Pending",
            contact_last_name="Provider",
            phone_number="+15145550208",
            email="incomplete.provider@example.com",
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
                "action": "accept_terms",
            },
        )

        self.assertRedirects(response, reverse("provider_complete_profile"))
        provider.refresh_from_db()
        self.assertFalse(provider.profile_completed)
        self.assertTrue(provider.accepts_terms)

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
                "entity_type": "company",
                "legal_name": "Acme Legal",
                "business_name": "Acme Services",
                "province": "QC",
                "city": "Montreal",
                "postal_code": "H1A1A1",
                "address_line1": "200 Business Ave",
            },
        )

        self.assertRedirects(response, reverse("portal:provider_dashboard"))
        provider.refresh_from_db()
        self.assertTrue(provider.billing_profile_completed)
        self.assertEqual(provider.city, "Montreal")

    def test_provider_edit_creates_provider_location_from_geocode(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Jane Smith",
            contact_first_name="Jane",
            contact_last_name="Smith",
            phone_number="+15145550901",
            email="provider.location.edit@example.com",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="123 Provider St",
        )
        self.geocode_address_mock.return_value = {
            "lat": 45.5601,
            "lng": -73.7124,
            "components": [],
        }

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.post(
            reverse("provider_edit"),
            data={
                "provider_type": provider.provider_type,
                "company_name": provider.company_name or "",
                "legal_name": provider.legal_name,
                "business_registration_number": provider.business_registration_number,
                "employee_count": provider.employee_count,
                "contact_first_name": provider.contact_first_name,
                "contact_last_name": provider.contact_last_name,
                "phone_number": provider.phone_number,
                "email": provider.email,
                "languages_spoken": provider.languages_spoken,
                "country": "Canada",
                "province": "QC",
                "city": "Laval",
                "postal_code": "H7A1A1",
                "address_line1": "123 Updated St",
                "availability_mode": provider.availability_mode,
                "service_radius_km": str(provider.service_radius_km),
            },
        )

        self.assertRedirects(response, reverse("provider_profile"))
        location = ProviderLocation.objects.get(provider=provider)
        self.assertEqual(location.latitude, Decimal("45.560100"))
        self.assertEqual(location.longitude, Decimal("-73.712400"))
        self.assertEqual(location.city, "Laval")
        self.assertEqual(location.province, "QC")
        self.assertEqual(location.postal_code, "H7A1A1")
        self.geocode_address_mock.assert_called_once_with(
            "H7A1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )

    def test_provider_complete_billing_creates_provider_location_from_geocode(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Acme Services",
            contact_first_name="Jane",
            contact_last_name="Manager",
            phone_number="+15145550902",
            email="provider.location.billing@example.com",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=False,
            accepts_terms=True,
            country="Canada",
            province="QC",
            city="Pending",
            postal_code="PENDING",
            address_line1="Pending profile completion",
        )
        self.geocode_address_mock.return_value = {
            "lat": 45.5017,
            "lng": -73.5673,
            "components": [],
        }

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.post(
            reverse("provider_complete_billing"),
            data={
                "entity_type": "company",
                "legal_name": "Acme Legal",
                "business_name": "Acme Services",
                "province": "QC",
                "city": "Montreal",
                "postal_code": "H1A1A1",
                "address_line1": "200 Business Ave",
            },
        )

        self.assertRedirects(response, reverse("portal:provider_dashboard"))
        location = ProviderLocation.objects.get(provider=provider)
        self.assertEqual(location.latitude, Decimal("45.501700"))
        self.assertEqual(location.longitude, Decimal("-73.567300"))
        self.assertEqual(location.city, "Montreal")
        self.assertEqual(location.province, "QC")
        self.assertEqual(location.postal_code, "H1A1A1")
        self.geocode_address_mock.assert_called_once_with(
            "H1A1A1",
            city="Montreal",
            province="QC",
            country="Canada",
        )

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

        service_type = ServiceType.objects.create(name="Cleaning", description="Cleaning")
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Deep Cleaning",
            description="",
            billing_unit="hour",
            price_cents=10000,
            is_active=True,
        )

        self.assertTrue(provider.has_active_service())
        self.assertTrue(provider.is_operational)

    def test_provider_is_not_operational_when_required_certification_is_missing(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Jane Smith",
            contact_first_name="Jane",
            contact_last_name="Smith",
            phone_number="+15145550231",
            email="missing.cert.provider@example.com",
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
        service_type = ServiceType.objects.create(name="Plumbing", description="Plumbing")
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Plumbing Service",
            description="",
            billing_unit="hour",
            price_cents=12000,
            is_active=True,
        )
        RequiredCertification.objects.create(
            service_type=service_type,
            province="QC",
            requires_certificate=True,
            certificate_type="RBQ",
        )

        self.assertFalse(provider.has_required_certifications)
        self.assertFalse(provider.is_operational)

    def test_provider_is_operational_when_required_certification_is_verified(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Jane Smith",
            contact_first_name="Jane",
            contact_last_name="Smith",
            phone_number="+15145550232",
            email="verified.cert.provider@example.com",
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
        service_type = ServiceType.objects.create(name="Gas Technician", description="Gas")
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Gas Service",
            description="",
            billing_unit="hour",
            price_cents=13000,
            is_active=True,
        )
        RequiredCertification.objects.create(
            service_type=service_type,
            province="QC",
            requires_certificate=True,
            certificate_type="TSSA",
        )
        ProviderCertificate.objects.create(
            provider=provider,
            cert_type="TSSA",
            status=ProviderCertificate.Status.VERIFIED,
        )

        self.assertTrue(provider.has_required_certifications)
        self.assertTrue(provider.is_operational)

    def test_provider_dashboard_redirects_provider_without_services_to_portal_dashboard(self):
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

        response = self.client.get(reverse("provider_dashboard"), follow=True)

        self.assertRedirects(response, reverse("portal:provider_dashboard"))
        self.assertContains(response, "Provider Dashboard")
        self.assertContains(response, "No jobs yet.")

    def test_provider_dashboard_allows_billing_incomplete_provider_into_portal_dashboard(self):
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

        response = self.client.get(reverse("provider_dashboard"), follow=True)

        self.assertRedirects(response, reverse("portal:provider_dashboard"))
        self.assertContains(response, "Provider Dashboard")
        self.assertContains(response, "No jobs yet.")

    def test_provider_dashboard_redirects_ready_provider_to_portal_dashboard(self):
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
        service_type = ServiceType.objects.create(name="Plumbing", description="Plumbing")
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Leak Repair",
            description="",
            billing_unit="fixed",
            price_cents=18000,
            is_active=True,
        )
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("provider_dashboard"), follow=True)

        self.assertRedirects(response, reverse("portal:provider_dashboard"))
        self.assertContains(response, "Provider Dashboard")
        self.assertContains(response, "No jobs yet.")
