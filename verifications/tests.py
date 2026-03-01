import json
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client as HttpClient
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Client

from .models import PhoneVerification, SecurityEvent
from .services import MAX_OTP_ATTEMPTS, create_phone_verification, verify_phone_code


class PhoneVerificationApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.http = HttpClient()
        self.client_actor = Client.objects.create(
            first_name="Otp",
            last_name="Client",
            phone_number="+15145550000",
            email="otp-client@example.com",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="123 Test St",
        )

    def test_request_phone_verification_creates_pending_record(self):
        response = self.http.post(
            reverse("request_phone_verification"),
            data=json.dumps(
                {
                    "actor_type": "client",
                    "actor_id": self.client_actor.pk,
                    "phone_number": self.client_actor.phone_number,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["detail"], "Verification created.")
        self.assertIn("expires_at", body)
        self.assertTrue(
            PhoneVerification.objects.filter(
                actor_type="client",
                actor_id=self.client_actor.pk,
                is_verified=False,
            ).exists()
        )

    def test_request_phone_verification_enforces_cooldown(self):
        url = reverse("request_phone_verification")
        payload = json.dumps(
            {
                "actor_type": "client",
                "actor_id": self.client_actor.pk,
                "phone_number": self.client_actor.phone_number,
            }
        )

        first_response = self.http.post(
            url,
            data=payload,
            content_type="application/json",
        )
        second_response = self.http.post(
            url,
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 429)
        self.assertEqual(
            second_response.json()["detail"],
            "Please wait before requesting another code.",
        )

    def test_request_phone_verification_rate_limits_by_phone_number(self):
        shared_phone = "+15145559999"
        actors = [
            Client.objects.create(
                first_name=f"Otp{i}",
                last_name="Shared",
                phone_number=shared_phone,
                email=f"otp-shared-{i}@example.com",
                country="Canada",
                province="QC",
                city="Montreal",
                postal_code="H1A1A1",
                address_line1="123 Shared St",
            )
            for i in range(1, 5)
        ]
        url = reverse("request_phone_verification")

        for actor in actors[:3]:
            response = self.http.post(
                url,
                data=json.dumps(
                    {
                        "actor_type": "client",
                        "actor_id": actor.pk,
                        "phone_number": shared_phone,
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 201)

        blocked_response = self.http.post(
            url,
            data=json.dumps(
                {
                    "actor_type": "client",
                    "actor_id": actors[3].pk,
                    "phone_number": shared_phone,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(blocked_response.status_code, 429)
        self.assertEqual(
            blocked_response.json()["detail"],
            "Too many requests. Try later.",
        )
        self.assertTrue(
            SecurityEvent.objects.filter(
                event_type=SecurityEvent.EventType.OTP_PHONE_RATE_LIMIT,
                phone_number=shared_phone,
            ).exists()
        )

    def test_request_phone_verification_enforces_daily_limit(self):
        daily_key = f"otp_daily:{self.client_actor.phone_number}:{timezone.now().date()}"
        cache.set(daily_key, 10, timeout=86400)

        response = self.http.post(
            reverse("request_phone_verification"),
            data=json.dumps(
                {
                    "actor_type": "client",
                    "actor_id": self.client_actor.pk,
                    "phone_number": self.client_actor.phone_number,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["detail"], "Daily limit reached.")
        self.assertTrue(
            SecurityEvent.objects.filter(
                event_type=SecurityEvent.EventType.OTP_DAILY_LIMIT,
                phone_number=self.client_actor.phone_number,
            ).exists()
        )

    def test_confirm_phone_verification_marks_actor_verified(self):
        code, _ = create_phone_verification(
            actor_type="client",
            actor_id=self.client_actor.pk,
            phone_number=self.client_actor.phone_number,
        )

        response = self.http.post(
            reverse("confirm_phone_verification"),
            data=json.dumps(
                {
                    "actor_type": "CLIENT",
                    "actor_id": self.client_actor.pk,
                    "code": code,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["detail"],
            "Phone verified successfully.",
        )

        self.client_actor.refresh_from_db()
        verification = PhoneVerification.objects.get(
            actor_type="client",
            actor_id=self.client_actor.pk,
        )
        self.assertTrue(self.client_actor.is_phone_verified)
        self.assertIsNotNone(self.client_actor.phone_verified_at)
        self.assertTrue(verification.is_verified)
        self.assertIsNotNone(verification.verified_at)

    @patch("verifications.services.send_sms")
    def test_create_phone_verification_sends_sms(self, send_sms_mock):
        code, verification = create_phone_verification(
            actor_type="client",
            actor_id=self.client_actor.pk,
            phone_number=self.client_actor.phone_number,
        )

        self.assertEqual(len(code), 6)
        self.assertEqual(verification.phone_number, self.client_actor.phone_number)
        send_sms_mock.assert_called_once_with(
            phone_number=self.client_actor.phone_number,
            message=f"Your verification code is: {code}",
        )

    def test_verify_phone_code_blocks_number_after_max_attempts(self):
        create_phone_verification(
            actor_type="client",
            actor_id=self.client_actor.pk,
            phone_number=self.client_actor.phone_number,
        )

        for _ in range(MAX_OTP_ATTEMPTS - 1):
            with self.assertRaisesMessage(ValidationError, "Invalid code."):
                verify_phone_code("client", self.client_actor.pk, "000000")

        with self.assertRaisesMessage(ValidationError, "Too many attempts."):
            verify_phone_code("client", self.client_actor.pk, "000000")

        self.assertTrue(cache.get(f"otp_block:{self.client_actor.phone_number}"))
        self.assertTrue(
            SecurityEvent.objects.filter(
                event_type=SecurityEvent.EventType.OTP_ABUSE_BLOCK,
                phone_number=self.client_actor.phone_number,
            ).exists()
        )

        with self.assertRaisesMessage(ValidationError, "Too many attempts."):
            verify_phone_code("client", self.client_actor.pk, "000000")
