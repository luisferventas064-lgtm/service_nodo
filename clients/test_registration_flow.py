from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from clients.models import Client
from verifications.services import create_phone_verification


class ClientRegistrationFlowTests(TestCase):
    def test_client_register_creates_unverified_client_and_redirects_to_verify(self):
        response = self.client.post(
            reverse("client_register"),
            data={
                "full_name": "Jane Doe",
                "email": "jane.doe@example.com",
                "phone_number": "+15145550123",
            },
        )

        self.assertRedirects(response, reverse("verify_phone"))
        client = Client.objects.get(email="jane.doe@example.com")
        self.assertFalse(client.is_phone_verified)
        self.assertFalse(client.profile_completed)
        self.assertEqual(self.client.session["verify_actor_type"], "client")
        self.assertEqual(self.client.session["verify_actor_id"], client.pk)

    @patch("verifications.services.send_sms")
    def test_verify_phone_page_redirects_to_complete_profile(self, send_sms_mock):
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
        code, _ = create_phone_verification(
            actor_type="client",
            actor_id=client.pk,
            phone_number=client.phone_number,
        )

        session = self.client.session
        session["verify_actor_type"] = "client"
        session["verify_actor_id"] = client.pk
        session.save()

        response = self.client.post(
            reverse("verify_phone"),
            data={"code": code},
        )

        self.assertRedirects(response, reverse("client_complete_profile"))
        client.refresh_from_db()
        self.assertTrue(client.is_phone_verified)
        self.assertEqual(self.client.session["client_id"], client.pk)

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
            },
        )

        self.assertRedirects(response, reverse("client_dashboard"))
        client.refresh_from_db()
        self.assertTrue(client.profile_completed)
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
