from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from clients.models import Client
from ui.models import PasswordResetCode


class ClientRegistrationFlowTests(TestCase):
    @patch("clients.views.send_sms")
    def test_client_register_creates_unverified_client_and_redirects_to_verify(self, send_sms_mock):
        response = self.client.post(
            reverse("client_register"),
            data={
                "full_name": "Jane Doe",
                "email": "jane.doe@example.com",
                "country": "CA",
                "phone_local": "4388365523",
                "password": "test-pass-123",
                "confirm_password": "test-pass-123",
            },
        )

        self.assertRedirects(response, reverse("verify_phone"))
        client = Client.objects.get(email="jane.doe@example.com")
        self.assertEqual(client.phone_number, "+14388365523")
        self.assertFalse(client.is_phone_verified)
        self.assertFalse(client.profile_completed)
        self.assertEqual(self.client.session["verify_phone"], client.phone_number)
        self.assertEqual(self.client.session["verify_role"], "client")
        self.assertEqual(self.client.session["verify_actor_type"], "client")
        self.assertEqual(self.client.session["verify_actor_id"], client.pk)
        self.assertTrue(
            PasswordResetCode.objects.filter(
                phone_number=client.phone_number,
                purpose="verify",
            ).exists()
        )
        send_sms_mock.assert_called_once()

    def test_client_register_rejects_duplicate_email(self):
        Client.objects.create(
            first_name="Existing",
            last_name="Client",
            email="jane.doe@example.com",
            phone_number="+14388365529",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="123 Existing St",
        )

        response = self.client.post(
            reverse("client_register"),
            data={
                "full_name": "Jane Doe",
                "email": "Jane.Doe@example.com",
                "country": "CA",
                "phone_local": "4388365523",
                "password": "test-pass-123",
                "confirm_password": "test-pass-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A client with this email already exists.")
        self.assertEqual(Client.objects.filter(email__iexact="jane.doe@example.com").count(), 1)

    def test_client_register_rejects_duplicate_phone_number(self):
        Client.objects.create(
            first_name="Existing",
            last_name="Client",
            email="existing-client@example.com",
            phone_number="+14388365523",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="123 Existing St",
        )

        response = self.client.post(
            reverse("client_register"),
            data={
                "full_name": "Jane Doe",
                "email": "new-client@example.com",
                "country": "CA",
                "phone_local": "4388365523",
                "password": "test-pass-123",
                "confirm_password": "test-pass-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A client with this phone number already exists.")
        self.assertEqual(Client.objects.filter(phone_number="+14388365523").count(), 1)

    def test_client_register_rejects_password_mismatch(self):
        response = self.client.post(
            reverse("client_register"),
            data={
                "full_name": "Jane Doe",
                "email": "jane.mismatch@example.com",
                "country": "CA",
                "phone_local": "4388365528",
                "password": "test-pass-123",
                "confirm_password": "test-pass-124",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "There was a problem with your submission.")
        self.assertContains(response, "Passwords do not match.")
        self.assertFalse(Client.objects.filter(email="jane.mismatch@example.com").exists())

    def test_verify_phone_page_redirects_to_portal_router(self):
        client = Client.objects.create(
            first_name="Jane",
            last_name="Doe",
            email="verified.jane@example.com",
            phone_number="+15145550124",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="123 Test St",
        )
        PasswordResetCode.objects.create(
            phone_number=client.phone_number,
            code="123456",
            purpose="verify",
        )

        session = self.client.session
        session["verify_phone"] = client.phone_number
        session["verify_role"] = "client"
        session.save()

        response = self.client.post(
            reverse("verify_phone"),
            data={"code": "123456"},
        )

        self.assertRedirects(
            response,
            reverse("ui:portal"),
            fetch_redirect_response=False,
        )
        client.refresh_from_db()
        self.assertTrue(client.is_phone_verified)
        self.assertEqual(self.client.session["client_id"], client.pk)
        record = PasswordResetCode.objects.get(
            phone_number=client.phone_number,
            purpose="verify",
        )
        self.assertTrue(record.used)

    def test_client_complete_profile_marks_profile_completed(self):
        client = Client.objects.create(
            first_name="Jane",
            last_name="Doe",
            email="complete.profile@example.com",
            phone_number="+15145550125",
            is_phone_verified=True,
            country="Canada",
            province="QC",
            city="Pending",
            postal_code="PENDING",
            address_line1="Pending profile completion",
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            reverse("client_complete_profile"),
            data={
                "country": "Canada",
                "province": "QC",
                "city": "Montreal",
                "postal_code": "H1A1A1",
                "address_line1": "123 Test St",
                "accepts_terms": "on",
            },
        )

        self.assertRedirects(response, reverse("client_dashboard"))
        client.refresh_from_db()
        self.assertTrue(client.profile_completed)
        self.assertTrue(client.accepts_terms)
        self.assertEqual(client.city, "Montreal")

    def test_client_dashboard_redirects_to_complete_profile_until_completed(self):
        client = Client.objects.create(
            first_name="Jane",
            last_name="Doe",
            email="incomplete.profile@example.com",
            phone_number="+15145550126",
            is_phone_verified=True,
            profile_completed=False,
            country="Canada",
            province="QC",
            city="Pending",
            postal_code="PENDING",
            address_line1="Pending profile completion",
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.get(reverse("client_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your profile is incomplete.")
        self.assertContains(response, reverse("client_complete_profile"))
