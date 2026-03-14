from decimal import Decimal
from datetime import timedelta
import json
from unittest.mock import patch
from urllib.parse import quote, urlencode

from django.contrib.auth.hashers import check_password
from django.contrib.auth.hashers import make_password
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from assignments.models import JobAssignment
from clients.models import Client
from clients.models import ClientTicket
from jobs.models import Job, JobEvent, JobLocation, JobRequestedExtra, PlatformLedgerEntry
from notifications.models import PushDevice
from providers.models import (
    Provider,
    ProviderInsurance,
    ProviderLocation,
    ProviderService,
    ProviderServiceArea,
    ProviderServiceExtra,
    ProviderServiceSubservice,
    ProviderTicket,
    ProviderTicketLine,
)
from service_type.models import RequiredCertification, ServiceType
from workers.models import Worker

from .models import PasswordResetCode


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

    def test_staff_can_filter_quality_dashboard_by_provider_name(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="quality_dashboard_filter_staff",
            password="test-pass-123",
            is_staff=True,
        )
        self.client.force_login(user)

        matching_provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Alpha Quality Cleaners",
            legal_name="Alpha Quality Cleaners",
            business_registration_number="REG-ALPHA-001",
            contact_first_name="Alpha",
            contact_last_name="Team",
            phone_number="5550000001",
            email="alpha.quality@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="1 Alpha St",
        )
        other_provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Beta Quality Cleaners",
            legal_name="Beta Quality Cleaners",
            business_registration_number="REG-BETA-001",
            contact_first_name="Beta",
            contact_last_name="Team",
            phone_number="5550000002",
            email="beta.quality@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A2",
            address_line1="2 Beta St",
        )

        response = self.client.get(
            "/admin/quality/providers/",
            {"provider": "alpha"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, matching_provider.company_name)
        self.assertNotContains(response, other_provider.company_name)
        self.assertEqual(len(response.context["providers"]), 1)
        self.assertEqual(response.context["providers"][0].provider_id, matching_provider.provider_id)


class RegisterPushDeviceViewTests(TestCase):
    def test_register_push_device_creates_device_for_authenticated_user(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="push_device_user",
            password="test-pass-123",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("ui:register_push_device"),
            data=json.dumps(
                {
                    "role": "client",
                    "platform": "ios",
                    "token": "push-token-001",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        device = PushDevice.objects.get(token="push-token-001")
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "created": True,
                "device_id": device.pk,
                "role": "client",
                "platform": "ios",
                "is_active": True,
            },
        )
        self.assertEqual(device.user, user)
        self.assertEqual(device.role, PushDevice.Role.CLIENT)
        self.assertEqual(device.platform, PushDevice.Platform.IOS)
        self.assertTrue(device.is_active)

    def test_register_push_device_updates_existing_token(self):
        user_model = get_user_model()
        first_user = user_model.objects.create_user(
            username="push_device_first",
            password="test-pass-123",
        )
        second_user = user_model.objects.create_user(
            username="push_device_second",
            password="test-pass-123",
        )
        PushDevice.objects.create(
            user=first_user,
            role=PushDevice.Role.CLIENT,
            platform=PushDevice.Platform.IOS,
            token="push-token-shared",
            is_active=False,
        )
        self.client.force_login(second_user)

        response = self.client.post(
            reverse("ui:register_push_device"),
            data=json.dumps(
                {
                    "role": "provider",
                    "platform": "android",
                    "token": "push-token-shared",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        device = PushDevice.objects.get(token="push-token-shared")
        self.assertFalse(response.json()["created"])
        self.assertEqual(device.user, second_user)
        self.assertEqual(device.role, PushDevice.Role.PROVIDER)
        self.assertEqual(device.platform, PushDevice.Platform.ANDROID)
        self.assertTrue(device.is_active)

    def test_register_push_device_requires_authenticated_user(self):
        response = self.client.post(
            reverse("ui:register_push_device"),
            data=json.dumps(
                {
                    "role": "client",
                    "platform": "ios",
                    "token": "push-token-anon",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["ok"], False)

    def test_register_push_device_validates_payload(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="push_device_validation",
            password="test-pass-123",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("ui:register_push_device"),
            data=json.dumps(
                {
                    "role": "admin",
                    "platform": "web",
                    "token": "",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["ok"], False)
        self.assertEqual(PushDevice.objects.count(), 0)


class HomeViewTests(TestCase):
    def test_home_is_public_without_session(self):
        response = self.client.get(reverse("ui:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Find &amp; book trusted local services.")

    def test_root_home_is_public_without_session(self):
        response = self.client.get(reverse("ui:root_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Find &amp; book trusted local services.")

    def test_home_shows_navigation_links_for_authenticated_session(self):
        session = self.client.session
        session["client_id"] = 123
        session.save()

        response = self.client.get(reverse("ui:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Find &amp; book trusted local services.")
        self.assertContains(response, "Marketplace")
        self.assertContains(response, "Providers")
        self.assertContains(response, "Logout")

    def test_home_shows_logout_link_when_session_exists(self):
        session = self.client.session
        session["client_id"] = 123
        session.save()

        response = self.client.get(reverse("ui:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Logout")

    def test_home_links_client_session_to_client_dashboard(self):
        session = self.client.session
        session["nodo_role"] = "client"
        session["profile_id"] = 123
        session.save()

        response = self.client.get(reverse("ui:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("client_dashboard"))
        self.assertContains(response, "Client Dashboard")

    def test_logout_clears_manual_session_and_auth(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="logout_user",
            password="test-pass-123",
        )
        self.client.force_login(user)
        session = self.client.session
        session["client_id"] = 123
        session["provider_id"] = 456
        session["verify_actor_id"] = 789
        session["verify_actor_type"] = "client"
        session.save()

        response = self.client.get(reverse("ui:logout"))

        self.assertRedirects(response, reverse("ui:root_login"))
        self.assertNotIn("client_id", self.client.session)
        self.assertNotIn("provider_id", self.client.session)
        self.assertNotIn("verify_actor_id", self.client.session)
        self.assertNotIn("verify_actor_type", self.client.session)
        self.assertNotIn("_auth_user_id", self.client.session)


class LoginViewTests(TestCase):
    def test_root_login_selector_renders_role_choices(self):
        response = self.client.get(reverse("ui:root_login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome back")

    def test_login_selector_renders_role_choices(self):
        response = self.client.get(reverse("ui:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome back")
        self.assertContains(response, reverse("ui:login_client"))
        self.assertContains(response, reverse("ui:login_provider"))
        self.assertContains(response, reverse("ui:login_worker"))

    def test_client_login_sets_client_session(self):
        client_obj = Client.objects.create(
            first_name="Login",
            last_name="Client",
            phone_number="5550000300",
            email="login.client@test.local",
            password=make_password("test-pass-123"),
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="300 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )

        response = self.client.post(
            reverse("ui:login_client"),
            data={
                "identifier": "login.client@test.local",
                "password": "test-pass-123",
            },
        )

        self.assertRedirects(
            response,
            reverse("ui:portal"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.session["client_id"], client_obj.pk)

    def test_client_login_redirects_to_complete_profile_until_terms_are_accepted(self):
        client_obj = Client.objects.create(
            first_name="Terms",
            last_name="Client",
            phone_number="5550000301",
            email="terms.client@test.local",
            password=make_password("test-pass-123"),
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="301 Client St",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=False,
        )

        response = self.client.post(
            reverse("ui:login_client"),
            data={
                "identifier": "terms.client@test.local",
                "password": "test-pass-123",
            },
        )

        self.assertRedirects(
            response,
            reverse("ui:portal"),
            fetch_redirect_response=False,
        )
        client_obj.refresh_from_db()
        self.assertFalse(client_obj.profile_completed)
        self.assertEqual(self.client.session["client_id"], client_obj.pk)

    @patch("ui.views.send_sms")
    def test_unverified_client_login_redirects_to_verify_phone_and_sends_code(self, send_sms_mock):
        client_obj = Client.objects.create(
            first_name="Pending",
            last_name="Client",
            phone_number="5550000399",
            email="pending.client@test.local",
            password=make_password("test-pass-123"),
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="399 Client St",
            is_phone_verified=False,
            profile_completed=False,
        )

        response = self.client.post(
            reverse("ui:login_client"),
            data={
                "identifier": "pending.client@test.local",
                "password": "test-pass-123",
            },
        )

        self.assertRedirects(response, reverse("verify_phone"))
        self.assertEqual(self.client.session["verify_phone"], client_obj.phone_number)
        self.assertEqual(self.client.session["verify_role"], "client")
        self.assertTrue(
            PasswordResetCode.objects.filter(
                phone_number=client_obj.phone_number,
                purpose="verify",
                used=False,
            ).exists()
        )
        send_sms_mock.assert_called_once()

    def test_provider_login_sets_provider_session(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            legal_name="Login Provider",
            contact_first_name="Login",
            contact_last_name="Provider",
            phone_number="+14388365523",
            email="login.provider@test.local",
            password=make_password("test-pass-123"),
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="301 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area="Montreal",
        )

        response = self.client.post(
            reverse("ui:login_provider"),
            data={
                "identifier": "4388365523",
                "password": "test-pass-123",
            },
        )

        self.assertRedirects(
            response,
            reverse("ui:portal"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.session["provider_id"], provider.pk)

    def test_incomplete_provider_login_redirects_to_provider_complete_profile(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Pending",
            contact_last_name="Provider",
            phone_number="+14388365524",
            email="pending.provider@test.local",
            password=make_password("test-pass-123"),
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="302 Provider St",
            is_phone_verified=True,
            profile_completed=False,
            billing_profile_completed=False,
            accepts_terms=False,
        )

        response = self.client.post(
            reverse("ui:login_provider"),
            data={
                "identifier": "4388365524",
                "password": "test-pass-123",
            },
        )

        self.assertRedirects(
            response,
            reverse("ui:portal"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.session["provider_id"], provider.pk)

    def test_worker_login_sets_worker_session(self):
        worker = Worker.objects.create(
            first_name="Login",
            last_name="Worker",
            phone_number="5550000302",
            email="login.worker@test.local",
            password=make_password("test-pass-123"),
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )

        response = self.client.post(
            reverse("ui:login_worker"),
            data={
                "identifier": "login.worker@test.local",
                "password": "test-pass-123",
            },
        )

        self.assertRedirects(
            response,
            reverse("ui:portal"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.session["worker_id"], worker.pk)

    def test_incomplete_worker_login_redirects_to_worker_profile(self):
        worker = Worker.objects.create(
            first_name="Pending",
            last_name="Worker",
            phone_number="5550000303",
            email="pending.complete.worker@test.local",
            password=make_password("test-pass-123"),
            is_phone_verified=True,
            accepts_terms=False,
            profile_completed=True,
        )

        response = self.client.post(
            reverse("ui:login_worker"),
            data={
                "identifier": "pending.complete.worker@test.local",
                "password": "test-pass-123",
            },
        )

        self.assertRedirects(
            response,
            reverse("ui:portal"),
            fetch_redirect_response=False,
        )
        worker.refresh_from_db()
        self.assertFalse(worker.profile_completed)
        self.assertEqual(self.client.session["worker_id"], worker.pk)

    @patch("ui.views.send_sms")
    def test_unverified_worker_login_redirects_to_verify_phone_and_sends_code(self, send_sms_mock):
        worker = Worker.objects.create(
            first_name="Pending",
            last_name="Worker",
            phone_number="4389216949",
            email="pending.worker@test.local",
            password=make_password("test-pass-123"),
            is_phone_verified=False,
        )

        response = self.client.post(
            reverse("ui:login_worker"),
            data={
                "identifier": "4389216949",
                "password": "test-pass-123",
            },
        )

        self.assertRedirects(response, reverse("verify_phone"))
        self.assertEqual(self.client.session["verify_phone"], worker.phone_number)
        self.assertEqual(self.client.session["verify_role"], "worker")
        self.assertTrue(
            PasswordResetCode.objects.filter(
                phone_number=worker.phone_number,
                purpose="verify",
                used=False,
            ).exists()
        )
        send_sms_mock.assert_called_once()

    def test_client_login_shows_alert_for_invalid_credentials(self):
        response = self.client.post(
            reverse("ui:login_client"),
            data={
                "identifier": "missing@test.local",
                "password": "wrong-pass",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="alert alert-error"')
        self.assertContains(response, "Login failed.")
        self.assertContains(response, "Invalid credentials.")
        self.assertContains(response, 'class="form-input input-error"', count=2)
        self.assertContains(response, 'class="password-toggle"')
        self.assertContains(response, "togglePasswordVisibility(this)")

    def test_provider_login_shows_alert_for_invalid_credentials(self):
        response = self.client.post(
            reverse("ui:login_provider"),
            data={
                "identifier": "missing.provider@test.local",
                "password": "wrong-pass",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="alert alert-error"')
        self.assertContains(response, "Login failed.")
        self.assertContains(response, "Invalid credentials.")
        self.assertContains(response, 'class="form-input input-error"', count=2)
        self.assertContains(response, 'class="password-toggle"')
        self.assertContains(response, "togglePasswordVisibility(this)")

    def test_worker_login_shows_alert_for_invalid_credentials(self):
        response = self.client.post(
            reverse("ui:login_worker"),
            data={
                "identifier": "missing.worker@test.local",
                "password": "wrong-pass",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="alert alert-error"')
        self.assertContains(response, "Login failed.")
        self.assertContains(response, "Invalid credentials.")
        self.assertContains(response, 'class="form-input input-error"', count=2)
        self.assertContains(response, 'class="password-toggle"')
        self.assertContains(response, "togglePasswordVisibility(this)")

    @patch("ui.views.send_sms")
    def test_forgot_password_creates_reset_code_and_redirects(self, send_sms_mock):
        Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Reset",
            contact_last_name="Match",
            phone_number="+14388365523",
            email="reset.match@test.local",
            password=make_password("test-pass-123"),
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="401 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
        )
        response = self.client.post(
            reverse("ui:forgot_password"),
            data={
                "phone": "4388365523",
            },
        )

        self.assertRedirects(response, reverse("ui:reset_password_confirm"))
        reset_code = PasswordResetCode.objects.get(phone_number="+14388365523")
        self.assertEqual(len(reset_code.code), 6)
        self.assertEqual(reset_code.purpose, "reset")
        self.assertEqual(self.client.session["reset_phone"], "+14388365523")
        send_sms_mock.assert_called_once()

    @patch("ui.views.send_sms")
    def test_forgot_password_normalizes_phone_before_sending_without_account_match(self, send_sms_mock):
        response = self.client.post(
            reverse("ui:forgot_password"),
            data={
                "phone": "4388365523",
            },
        )

        self.assertRedirects(response, reverse("ui:reset_password_confirm"))
        self.assertEqual(self.client.session["reset_phone"], "+14388365523")
        send_sms_mock.assert_called_once()
        args, _ = send_sms_mock.call_args
        self.assertEqual(args[0], "+14388365523")
        self.assertIn("Your NODO reset code is:", args[1])

    def test_reset_password_confirm_redirects_without_phone_session(self):
        response = self.client.get(reverse("ui:reset_password_confirm"))

        self.assertRedirects(response, reverse("ui:forgot_password"))

    def test_reset_password_confirm_updates_matching_account_password(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Reset",
            contact_last_name="Provider",
            phone_number="4388365523",
            email="reset.provider@test.local",
            password=make_password("old-pass-123"),
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="400 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
        )
        PasswordResetCode.objects.create(
            phone_number="+14388365523",
            code="123456",
            purpose="reset",
        )
        session = self.client.session
        session["reset_phone"] = "+14388365523"
        session.save()

        response = self.client.post(
            reverse("ui:reset_password_confirm"),
            data={
                "code": "123456",
                "new_password": "Nodo123!",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("ui:login"))
        provider.refresh_from_db()
        self.assertTrue(check_password("Nodo123!", provider.password))
        self.assertNotIn("reset_phone", self.client.session)
        self.assertContains(response, "Password updated. You can log in now.")

    def test_resend_code_rejects_missing_session(self):
        response = self.client.post(reverse("ui:resend_code"))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Session expired")

    def test_resend_code_rejects_recent_request_within_cooldown(self):
        session = self.client.session
        session["verify_phone"] = "+14388365523"
        session["verify_role"] = "provider"
        session.save()
        PasswordResetCode.objects.create(
            phone_number="+14388365523",
            code="111111",
            purpose="verify",
        )

        response = self.client.post(reverse("ui:resend_code"))

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"], "Please wait before requesting again")

    def test_resend_code_rejects_when_phone_limit_is_reached(self):
        session = self.client.session
        session["verify_phone"] = "+14388365523"
        session["verify_role"] = "provider"
        session.save()

        for code in ("111111", "222222", "333333"):
            record = PasswordResetCode.objects.create(
                phone_number="+14388365523",
                code=code,
                purpose="verify",
            )
            PasswordResetCode.objects.filter(pk=record.pk).update(
                created_at=timezone.now() - timedelta(minutes=2)
            )

        response = self.client.post(reverse("ui:resend_code"))

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"], "Too many attempts. Try later.")

    def test_resend_code_rejects_when_ip_limit_is_reached(self):
        session = self.client.session
        session["verify_phone"] = "+14388365523"
        session["verify_role"] = "provider"
        session.save()

        for index in range(10):
            record = PasswordResetCode.objects.create(
                phone_number=f"+14388365{index:03d}",
                code=f"{index:06d}",
                purpose="verify",
                ip_address="127.0.0.1",
            )
            PasswordResetCode.objects.filter(pk=record.pk).update(
                created_at=timezone.now() - timedelta(minutes=2)
            )

        response = self.client.post(reverse("ui:resend_code"))

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"], "Too many attempts from this network.")

    @patch("ui.views.send_sms")
    def test_resend_code_creates_new_verify_code_and_marks_previous_used(self, send_sms_mock):
        session = self.client.session
        session["verify_phone"] = "+14388365523"
        session["verify_role"] = "provider"
        session.save()
        old_code = PasswordResetCode.objects.create(
            phone_number="+14388365523",
            code="111111",
            purpose="verify",
        )
        PasswordResetCode.objects.filter(pk=old_code.pk).update(
            created_at=timezone.now() - timedelta(seconds=61)
        )

        response = self.client.post(reverse("ui:resend_code"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True})
        old_code.refresh_from_db()
        self.assertTrue(old_code.used)
        new_code = PasswordResetCode.objects.filter(
            phone_number="+14388365523",
            purpose="verify",
            used=False,
        ).latest("created_at")
        self.assertEqual(len(new_code.code), 6)
        self.assertIsNotNone(new_code.ip_address)
        send_sms_mock.assert_called_once()


class PortalViewTests(TestCase):

    def test_portal_redirects_to_login_when_no_manual_session_exists(self):
        response = self.client.get(reverse("ui:portal"))

        self.assertRedirects(
            response,
            reverse("ui:root_login"),
            fetch_redirect_response=False,
        )

    def test_portal_redirects_provider_session_to_provider_dashboard(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Portal",
            contact_last_name="Provider",
            phone_number="5550000098",
            email="portal.provider@test.local",
            profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="98 Provider St",
        )
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("ui:portal"))

        self.assertRedirects(
            response,
            reverse("portal:provider_dashboard"),
            fetch_redirect_response=False,
        )

    def test_portal_redirects_client_session_to_dashboard(self):
        client_obj = Client.objects.create(
            first_name="Portal",
            last_name="Client",
            phone_number="5550000099",
            email="portal.client@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="99 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("ui:portal"))

        self.assertRedirects(
            response,
            reverse("portal:client_dashboard"),
            fetch_redirect_response=False,
        )


class ProfileViewsTests(TestCase):
    def _create_client(self, *, first_name="Client", last_name="Visible", email="client.visible@test.local", phone_number="5550000100"):
        return Client.objects.create(
            first_name=first_name,
            last_name=last_name,
            phone_number=phone_number,
            email=email,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="10 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )

    def test_client_profile_alias_redirects_to_client_profile(self):
        client_obj = self._create_client(
            first_name="Alias",
            last_name="Client",
            email="alias.client@test.local",
            phone_number="5550000198",
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get("/client/profile/")

        self.assertRedirects(response, reverse("client_profile"))

    def test_provider_profile_alias_redirects_to_provider_profile(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            legal_name="Alias Provider",
            contact_first_name="Alias",
            contact_last_name="Provider",
            phone_number="5550000199",
            email="alias.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=False,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="199 Provider St",
        )
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get("/provider/profile/")

        self.assertRedirects(response, reverse("provider_profile"))

    def test_client_profile_is_visible_from_session(self):
        client_obj = self._create_client()
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("client_profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Client Profile")
        self.assertContains(response, "Client Visible")
        self.assertContains(response, "client.visible@test.local")

    def test_client_profile_shows_client_navigation_links(self):
        client_obj = self._create_client(
            first_name="Nav",
            last_name="Profile",
            email="nav.profile@test.local",
            phone_number="5550000102",
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("client_profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("client_dashboard"))
        self.assertContains(response, reverse("ui:marketplace_search"))
        self.assertContains(response, reverse("client_activity"))
        self.assertContains(response, reverse("client_billing"))
        self.assertContains(response, "Quick Links")
        self.assertContains(response, "Nav Profile \u2013 Client")
        self.assertNotContains(response, ">Account<", html=False)

    def test_client_activity_shows_client_navigation_links(self):
        client_obj = self._create_client(
            first_name="Nav",
            last_name="Activity",
            email="nav.activity@test.local",
            phone_number="5550000103",
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("client_activity"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("client_dashboard"))
        self.assertContains(response, reverse("ui:marketplace_search"))
        self.assertContains(response, reverse("client_profile"))
        self.assertContains(response, reverse("client_billing"))
        self.assertContains(response, "Nav Activity \u2013 Client")
        self.assertNotContains(response, ">Account<", html=False)

    def test_client_activity_shows_grouped_job_history_table(self):
        client_obj = self._create_client(
            first_name="History",
            last_name="Client",
            email="history.client@test.local",
            phone_number="5550000105",
        )
        provider = Provider.objects.create(
            provider_type="self_employed",
            legal_name="History Provider",
            contact_first_name="History",
            contact_last_name="Provider",
            phone_number="5550000106",
            email="history.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="55 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="House Cleaning",
            description="House Cleaning",
        )
        offer = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Deep Cleaning",
            billing_unit="fixed",
            price_cents=15000,
            is_active=True,
        )
        recorded_job = Job.objects.create(
            client=client_obj,
            selected_provider=provider,
            provider_service=offer,
            provider_service_name_snapshot="Deep Cleaning",
            service_type=service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.ASSIGNED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="10 Client St",
            requested_total_snapshot=Decimal("150.00"),
        )
        archived_job = Job.objects.create(
            client=client_obj,
            service_type=service_type,
            provider_service_name_snapshot="Archived Offer",
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.CANCELLED,
            cancelled_by=Job.CancellationActor.CLIENT,
            cancel_reason=Job.CancelReason.CLIENT_CANCELLED,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=2),
            scheduled_start_time=timezone.now().time().replace(second=0, microsecond=0),
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="99 Archive St",
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        ClientTicket.objects.create(
            client=client_obj,
            ref_type="job",
            ref_id=recorded_job.job_id,
            ticket_no="CT-HISTORY-001",
            status=ClientTicket.Status.FINALIZED,
            total_cents=15_000,
        )
        archived_job.financial.status = "draft"
        archived_job.financial.save(update_fields=["status"])

        response = self.client.get(reverse("client_activity"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity History")
        self.assertContains(response, recorded_job.public_reference)
        self.assertContains(response, "House Cleaning")
        self.assertContains(response, "Deep Cleaning")
        self.assertContains(response, "Archived Offer")
        self.assertContains(response, "Searching...")
        self.assertContains(response, "Finalized")
        self.assertContains(response, "Draft")
        self.assertContains(response, "Total charged")
        self.assertNotContains(response, "Provider earnings")
        self.assertNotContains(response, "Platform fee")
        self.assertContains(response, "Client - Client cancelled")
        self.assertContains(response, reverse("ui:request_status", args=[recorded_job.job_id]))
        self.assertContains(response, reverse("ui:request_status", args=[archived_job.job_id]))

    def test_client_activity_filters_jobs_by_selected_status(self):
        client_obj = self._create_client(
            first_name="Filter",
            last_name="Client",
            email="filter.client@test.local",
            phone_number="5550000107",
        )
        service_type = ServiceType.objects.create(
            name="Filter Service",
            description="Filter Service",
        )
        posted_job = Job.objects.create(
            client=client_obj,
            service_type=service_type,
            provider_service_name_snapshot="Posted Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="10 Client St",
        )
        assigned_job = Job.objects.create(
            client=client_obj,
            service_type=service_type,
            provider_service_name_snapshot="Assigned Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.ASSIGNED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="10A Client St",
        )
        in_progress_job = Job.objects.create(
            client=client_obj,
            service_type=service_type,
            provider_service_name_snapshot="In Progress Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.IN_PROGRESS,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A3",
            address_line1="10B Client St",
        )
        completed_job = Job.objects.create(
            client=client_obj,
            service_type=service_type,
            provider_service_name_snapshot="Completed Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="11 Client St",
            requested_total_snapshot=Decimal("120.00"),
        )
        confirmed_job = Job.objects.create(
            client=client_obj,
            service_type=service_type,
            provider_service_name_snapshot="Confirmed Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.CONFIRMED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Longueuil",
            postal_code="J4K1A1",
            address_line1="11B Client St",
            requested_total_snapshot=Decimal("140.00"),
        )
        cancelled_job = Job.objects.create(
            client=client_obj,
            service_type=service_type,
            provider_service_name_snapshot="Cancelled Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.CANCELLED,
            cancelled_by=Job.CancellationActor.CLIENT,
            cancel_reason=Job.CancelReason.CLIENT_CANCELLED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Quebec",
            postal_code="G1A0A2",
            address_line1="12 Client St",
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        posted_response = self.client.get(reverse("client_activity"), {"status": "posted"})
        assigned_response = self.client.get(reverse("client_activity"), {"status": "assigned"})
        in_progress_response = self.client.get(reverse("client_activity"), {"status": "in_progress"})
        completed_response = self.client.get(reverse("client_activity"), {"status": "completed"})
        cancelled_response = self.client.get(reverse("client_activity"), {"status": "cancelled"})
        invalid_response = self.client.get(reverse("client_activity"), {"status": "unknown"})

        self.assertContains(posted_response, "Posted Offer")
        self.assertNotContains(posted_response, "Assigned Offer")
        self.assertNotContains(posted_response, "Completed Offer")
        self.assertNotContains(posted_response, "Cancelled Offer")

        self.assertContains(assigned_response, "Assigned Offer")
        self.assertNotContains(assigned_response, "Posted Offer")
        self.assertNotContains(assigned_response, "In Progress Offer")
        self.assertNotContains(assigned_response, "Cancelled Offer")

        self.assertContains(in_progress_response, "In Progress Offer")
        self.assertNotContains(in_progress_response, "Assigned Offer")
        self.assertNotContains(in_progress_response, "Completed Offer")
        self.assertNotContains(in_progress_response, "Cancelled Offer")

        self.assertContains(completed_response, "Completed Offer")
        self.assertContains(completed_response, "Confirmed Offer")
        self.assertNotContains(completed_response, "Posted Offer")
        self.assertNotContains(completed_response, "Assigned Offer")
        self.assertNotContains(completed_response, "Cancelled Offer")

        self.assertContains(cancelled_response, "Cancelled Offer")
        self.assertContains(cancelled_response, "Client - Client cancelled")
        self.assertNotContains(cancelled_response, "Posted Offer")
        self.assertNotContains(cancelled_response, "Assigned Offer")
        self.assertNotContains(cancelled_response, "Completed Offer")

        self.assertContains(invalid_response, posted_job.public_reference)
        self.assertContains(invalid_response, assigned_job.public_reference)
        self.assertContains(invalid_response, in_progress_job.public_reference)
        self.assertContains(invalid_response, completed_job.public_reference)
        self.assertContains(invalid_response, confirmed_job.public_reference)
        self.assertContains(invalid_response, cancelled_job.public_reference)

    def test_client_activity_shows_filter_counts(self):
        client_obj = self._create_client(
            first_name="Count",
            last_name="Client",
            email="count.client@test.local",
            phone_number="5550000108",
        )
        service_type = ServiceType.objects.create(
            name="Count Service",
            description="Count Service",
        )
        for status in (
            Job.JobStatus.POSTED,
            Job.JobStatus.POSTED,
            Job.JobStatus.ASSIGNED,
            Job.JobStatus.IN_PROGRESS,
            Job.JobStatus.COMPLETED,
            Job.JobStatus.CONFIRMED,
            Job.JobStatus.CANCELLED,
        ):
            job_kwargs = {
                "client": client_obj,
                "service_type": service_type,
                "provider_service_name_snapshot": f"{status} offer",
                "job_mode": Job.JobMode.ON_DEMAND,
                "job_status": status,
                "is_asap": True,
                "country": "Canada",
                "province": "QC",
                "city": "Montreal",
                "postal_code": "H1A1A1",
                "address_line1": "15 Count St",
            }
            if status == Job.JobStatus.CANCELLED:
                job_kwargs["cancelled_by"] = Job.CancellationActor.CLIENT
                job_kwargs["cancel_reason"] = Job.CancelReason.CLIENT_CANCELLED
            Job.objects.create(
                **job_kwargs,
            )

        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("client_activity"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "All (7)")
        self.assertContains(response, "Posted (2)")
        self.assertContains(response, "Assigned (1)")
        self.assertContains(response, "In progress (1)")
        self.assertContains(response, "Completed (2)")
        self.assertContains(response, "Cancelled (1)")

    def test_client_activity_supports_second_page(self):
        client_obj = self._create_client(
            first_name="Paged",
            last_name="Client",
            email="paged.client@test.local",
            phone_number="5550000109",
        )
        service_type = ServiceType.objects.create(
            name="Paged Service",
            description="Paged Service",
        )
        for index in range(11):
            Job.objects.create(
                client=client_obj,
                service_type=service_type,
                provider_service_name_snapshot=f"Paged Offer {index}",
                job_mode=Job.JobMode.ON_DEMAND,
                job_status=Job.JobStatus.POSTED,
                is_asap=True,
                country="Canada",
                province="QC",
                city="Montreal",
                postal_code="H1A1A1",
                address_line1="16 Count St",
            )

        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("client_activity"), {"page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertTrue(response.context["is_paginated"])
        self.assertContains(response, "Page 2 of 2")

    def test_client_activity_supports_date_range_filter(self):
        client_obj = self._create_client(
            first_name="Range",
            last_name="Client",
            email="range.client@test.local",
            phone_number="5550000110",
        )
        service_type = ServiceType.objects.create(
            name="Range Service",
            description="Range Service",
        )
        recent_job = Job.objects.create(
            client=client_obj,
            service_type=service_type,
            provider_service_name_snapshot="Recent Range Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="17 Range St",
        )
        old_job = Job.objects.create(
            client=client_obj,
            service_type=service_type,
            provider_service_name_snapshot="Old Range Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="18 Range St",
        )
        Job.objects.filter(pk=recent_job.pk).update(created_at=timezone.now() - timedelta(days=2))
        Job.objects.filter(pk=old_job.pk).update(created_at=timezone.now() - timedelta(days=8))

        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("client_activity"), {"range": "7d"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_range"], "7d")
        self.assertContains(response, "Recent Range Offer")
        self.assertNotContains(response, "Old Range Offer")

    def test_client_activity_query_count_stays_reasonable(self):
        client_obj = self._create_client(
            first_name="Perf",
            last_name="Client",
            email="perf.client@test.local",
            phone_number="5550000111",
        )
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Perf Provider",
            contact_first_name="Perf",
            contact_last_name="Provider",
            phone_number="5550000112",
            email="perf.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="19 Perf St",
        )
        service_type = ServiceType.objects.create(
            name="Perf Service",
            description="Perf Service",
        )
        for index in range(3):
            Job.objects.create(
                client=client_obj,
                selected_provider=provider,
                service_type=service_type,
                provider_service_name_snapshot=f"Perf Offer {index}",
                job_mode=Job.JobMode.ON_DEMAND,
                job_status=Job.JobStatus.POSTED,
                is_asap=True,
                country="Canada",
                province="QC",
                city="Montreal",
                postal_code="H1A1A1",
                address_line1="20 Perf St",
            )

        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse("client_activity"))

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(ctx), 15)

    def test_client_activity_supports_sort_filter(self):
        client_obj = self._create_client(
            first_name="Sort",
            last_name="Client",
            email="sort.client@test.local",
            phone_number="5550000113",
        )
        service_type = ServiceType.objects.create(
            name="Sort Service",
            description="Sort Service",
        )
        older_job = Job.objects.create(
            client=client_obj,
            service_type=service_type,
            provider_service_name_snapshot="Older Sort Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="21 Sort St",
        )
        newer_job = Job.objects.create(
            client=client_obj,
            service_type=service_type,
            provider_service_name_snapshot="Newer Sort Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="22 Sort St",
        )
        Job.objects.filter(pk=older_job.pk).update(created_at=timezone.now() - timedelta(days=5))
        Job.objects.filter(pk=newer_job.pk).update(created_at=timezone.now() - timedelta(days=1))

        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("client_activity"), {"sort": "oldest"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_sort"], "oldest")
        self.assertEqual(
            [row.job_id for row in response.context["jobs"][:2]],
            [older_job.job_id, newer_job.job_id],
        )

    def test_client_activity_exports_filtered_csv(self):
        client_obj = self._create_client(
            first_name="Export",
            last_name="Client",
            email="export.client@test.local",
            phone_number="5550000114",
        )
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Export Provider",
            contact_first_name="Export",
            contact_last_name="Provider",
            phone_number="5550000115",
            email="export.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="23 Export St",
        )
        service_type = ServiceType.objects.create(
            name="Export Service",
            description="Export Service",
        )
        recent_job = Job.objects.create(
            client=client_obj,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Recent Export Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="24 Export St",
        )
        old_job = Job.objects.create(
            client=client_obj,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Old Export Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="25 Export St",
        )
        Job.objects.filter(pk=recent_job.pk).update(created_at=timezone.now() - timedelta(days=2))
        Job.objects.filter(pk=old_job.pk).update(created_at=timezone.now() - timedelta(days=40))

        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(
            reverse("client_activity"),
            {
                "status": "completed",
                "range": "30d",
                "sort": "oldest",
                "export": "csv",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode("utf-8")
        self.assertIn(
            "Job ID,Date,Service,Provider,Status,Total charged,Cancelled Reason",
            content,
        )
        self.assertIn(str(recent_job.job_id), content)
        self.assertNotIn(str(old_job.job_id), content)

    def test_client_activity_shows_clear_filters_link(self):
        client_obj = self._create_client(
            first_name="Clear",
            last_name="Client",
            email="clear.client@test.local",
            phone_number="5550000116",
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(
            reverse("client_activity"),
            {
                "status": "completed",
                "range": "30d",
                "sort": "oldest",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'href="{reverse("client_activity")}" class="activity-clear-filters"',
            html=False,
        )
        self.assertContains(response, "Clear filters")

    def test_client_activity_detail_link_preserves_filter_state(self):
        client_obj = self._create_client(
            first_name="State",
            last_name="Client",
            email="state.client@test.local",
            phone_number="5550000117",
        )
        service_type = ServiceType.objects.create(
            name="State Service",
            description="State Service",
        )
        job = Job.objects.create(
            client=client_obj,
            service_type=service_type,
            provider_service_name_snapshot="State Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="26 State St",
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(
            reverse("client_activity"),
            {
                "status": "all",
                "range": "30d",
                "sort": "oldest",
            },
        )

        self.assertEqual(response.status_code, 200)
        expected_next = response.wsgi_request.get_full_path()
        expected_url = (
            f'{reverse("ui:request_status", args=[job.job_id])}?next='
            f'{quote(expected_next, safe="/")}'
        )
        self.assertContains(response, expected_url)

    def test_client_billing_shows_client_navigation_links(self):
        client_obj = self._create_client(
            first_name="Nav",
            last_name="Billing",
            email="nav.billing@test.local",
            phone_number="5550000104",
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("client_billing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("client_dashboard"))
        self.assertContains(response, reverse("ui:marketplace_search"))
        self.assertContains(response, reverse("client_activity"))
        self.assertContains(response, reverse("client_profile"))
        self.assertContains(response, "Nav Billing \u2013 Client")
        self.assertNotContains(response, ">Account<", html=False)

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
    def setUp(self):
        super().setUp()
        self.geocode_address_patcher = patch("ui.views.geocode_address", return_value=None)
        self.geocode_address_mock = self.geocode_address_patcher.start()
        self.addCleanup(self.geocode_address_patcher.stop)

    def test_request_create_get_shows_main_offers_for_selected_service_type(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Offers",
            phone_number="5550000100",
            email="provider.offers@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="30 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Offers Test",
            description="Offers Test",
        )
        cheaper_offer = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Standard cleaning",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )
        premium_offer = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Move-out cleaning",
            billing_unit="hour",
            price_cents=18000,
            is_active=True,
        )

        response = self.client.get(
            f"/request/{provider.provider_id}/?service_type_id={service_type.pk}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "javascript:history.back()")
        self.assertContains(response, 'id="request-confirm-modal"', html=False)
        self.assertContains(response, "Confirm Request")
        self.assertContains(response, "function openRequestModal(title, rowsHtml, options)")
        self.assertContains(response, "function showRequestError(message)")
        self.assertContains(response, "Request Error")
        self.assertNotContains(response, "window.confirm(")
        self.assertNotContains(response, "alert(")
        self.assertContains(response, "function formatDateHuman(dateValue)")
        self.assertContains(response, "function formatTimeHuman(timeValue)")
        self.assertContains(response, "Service option")
        self.assertContains(response, "Standard cleaning")
        self.assertContains(response, "Move-out cleaning")
        self.assertContains(response, "$120.00 / Fixed Price")
        self.assertContains(response, "$180.00 / Per Hour")
        self.assertContains(response, "Main Offer:")
        self.assertContains(response, cheaper_offer.custom_name)
        self.assertNotContains(response, f'data-provider-service-id="{premium_offer.pk}"')

    def test_request_create_get_shows_pricing_preview_with_taxes(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Preview",
            phone_number="5550000101",
            email="provider.preview@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="30 Provider Preview St",
        )
        service_type = ServiceType.objects.create(
            name="Preview Pricing",
            description="Preview Pricing",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Detailed cleaning",
            billing_unit="fixed",
            price_cents=15000,
            is_active=True,
        )

        response = self.client.get(
            f"/request/{provider.provider_id}/?service_type_id={service_type.pk}&provider_service_id={provider_service.pk}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pricing Preview")
        self.assertContains(response, "Base service")
        self.assertContains(response, "Subtotal")
        self.assertContains(response, "Taxes (")
        self.assertContains(response, ">QC<", html=False)
        self.assertContains(response, "Total")
        self.assertContains(response, "$150.00")
        self.assertContains(response, "$22.46")
        self.assertContains(response, "$172.46")
        self.assertContains(response, "Taxes calculated based on service location.")
        self.assertContains(response, "taxRegionCode: taxRegionCode,")
        self.assertNotContains(response, 'taxRegionCode: taxRegionCode || "DEFAULT"')

    def test_request_create_get_uses_requested_subservice_for_initial_pricing_preview(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Preview Subservice",
            phone_number="5550000108",
            email="provider.preview.subservice@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="37 Provider Preview St",
        )
        service_type = ServiceType.objects.create(
            name="Preview Subservice Pricing",
            description="Preview Subservice Pricing",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Starter cleaning",
            billing_unit="fixed",
            price_cents=7900,
            is_active=True,
        )
        deep_clean = ProviderServiceSubservice.objects.create(
            provider_service=provider_service,
            name="Deep Cleaning",
            base_price=Decimal("150.00"),
            is_active=True,
            sort_order=1,
        )

        response = self.client.get(
            f"/request/{provider.provider_id}/",
            {
                "service_type_id": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "requested_subservice_id": str(deep_clean.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form_data"]["requested_subservice_id"],
            str(deep_clean.pk),
        )
        pricing = response.context["pricing"]
        self.assertIsNotNone(pricing)
        self.assertEqual(pricing["base_unit_price"], Decimal("150.00"))
        self.assertEqual(pricing["base_line_total"], Decimal("150.00"))
        self.assertEqual(pricing["subtotal"], Decimal("150.00"))
        self.assertEqual(pricing["tax"], Decimal("22.46"))
        self.assertEqual(pricing["total"], Decimal("172.46"))
        self.assertNotContains(response, "Additional service detail")
        self.assertNotContains(response, "Choose one subservice")
        self.assertContains(response, 'id="request-pricing-unit-price">150.00<', html=False)
        self.assertContains(response, 'id="request-summary-unit-price">150.00<', html=False)
        self.assertContains(response, "$172.46")

    def test_request_create_preview_matches_job_snapshot(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Preview Sync",
            phone_number="5550000102",
            email="provider.preview.sync@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="31 Provider Preview St",
        )
        service_type = ServiceType.objects.create(
            name="Preview Sync Pricing",
            description="Preview Sync Pricing",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Snapshot cleaning",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Preview Sync",
            phone_number="5550000103",
            email="client.preview.sync@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="32 Client Preview St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        preview_response = self.client.get(
            f"/request/{provider.provider_id}/",
            {
                "service_type_id": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "province": "QC",
                "city": "Laval",
                "postal_code": "H7W4A2",
            },
        )

        self.assertEqual(preview_response.status_code, 200)
        preview = preview_response.context["pricing"]
        self.assertIsNotNone(preview)
        self.assertEqual(preview["subtotal_cents"], 10000)
        self.assertEqual(preview["tax_cents"], 1498)
        self.assertEqual(preview["total_cents"], 11498)
        self.assertEqual(preview["tax_region_code"], "QC")

        create_response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "job_mode": "on_demand",
            },
        )

        self.assertEqual(create_response.status_code, 302)
        job = client.jobs.get()
        self.assertEqual(job.requested_subtotal_snapshot, preview["subtotal"])
        self.assertEqual(job.requested_tax_snapshot, preview["tax"])
        self.assertEqual(job.requested_total_snapshot, preview["total"])
        self.assertEqual(job.requested_tax_region_code_snapshot, preview["tax_region_code"])
        self.assertEqual(job.requested_subtotal_snapshot, Decimal("100.00"))
        self.assertEqual(job.requested_tax_snapshot, Decimal("14.98"))
        self.assertEqual(job.requested_total_snapshot, Decimal("114.98"))

    def test_marketplace_request_flow_e2e_pricing_consistency(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="E2E Provider",
            contact_first_name="E2E",
            contact_last_name="Provider",
            phone_number="5550000104",
            email="provider.e2e.flow@test.local",
            province="QC",
            city="Laval",
            postal_code="H7W1A1",
            address_line1="33 Provider Flow St",
            service_area="Laval",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
        )
        service_type = ServiceType.objects.create(
            name="E2E Pricing Flow",
            description="E2E Pricing Flow",
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            postal_prefix="H7W",
            is_active=True,
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="E2E Cleaning",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="E2E",
            phone_number="5550000105",
            email="client.e2e.flow@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="34 Client Flow St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        marketplace_response = self.client.get(
            reverse("ui:marketplace_search"),
            {
                "service_type": service_type.pk,
                "service_timing": "urgent",
                "postal_code": "H7W4A2",
                "city": "Laval",
            },
        )

        self.assertEqual(marketplace_response.status_code, 200)
        self.assertContains(
            marketplace_response,
            f'href="{reverse("ui:request_create", args=[provider.pk])}',
            html=False,
        )
        self.assertContains(marketplace_response, "E2E Provider")
        self.assertContains(marketplace_response, "From $100.00")

        create_get_response = self.client.get(
            reverse("ui:request_create", args=[provider.provider_id]),
            {
                "service_type_id": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "service_timing": "urgent",
                "province": "QC",
                "city": "Laval",
                "postal_code": "H7W4A2",
            },
        )

        self.assertEqual(create_get_response.status_code, 200)
        preview = create_get_response.context["pricing"]
        self.assertIsNotNone(preview)
        self.assertEqual(preview["tax_region_code"], "QC")

        create_post_response = self.client.post(
            reverse("ui:request_create", args=[provider.provider_id]),
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "service_timing": "urgent",
                "job_mode": Job.JobMode.ON_DEMAND,
            },
        )

        self.assertEqual(create_post_response.status_code, 302)
        job = client.jobs.get()

        self.assertEqual(preview["subtotal"], job.requested_subtotal_snapshot)
        self.assertEqual(preview["tax"], job.requested_tax_snapshot)
        self.assertEqual(preview["total"], job.requested_total_snapshot)

        status_response = self.client.get(
            reverse("ui:request_status", args=[job.job_id])
        )

        self.assertEqual(status_response.status_code, 200)
        self.assertContains(status_response, "$100.00")
        self.assertContains(status_response, "$14.98")
        self.assertContains(status_response, "$114.98")
        self.assertContains(status_response, "Taxes (QC)")

    def test_marketplace_to_request_status_keeps_same_pricing(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Consistency Provider",
            contact_first_name="Consistency",
            contact_last_name="Provider",
            phone_number="5550000106",
            email="provider.consistency@test.local",
            province="QC",
            city="Laval",
            postal_code="H7W1B1",
            address_line1="35 Provider Consistency St",
            service_area="Laval",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
        )
        service_type = ServiceType.objects.create(
            name="Consistency Pricing",
            description="Consistency Pricing",
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            postal_prefix="H7W",
            is_active=True,
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Consistency Cleaning",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Consistency",
            phone_number="5550000107",
            email="client.consistency@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="36 Client Consistency St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        marketplace_response = self.client.get(
            reverse("ui:marketplace_search"),
            {
                "service_type": service_type.pk,
                "service_timing": "urgent",
                "postal_code": "H7W4A2",
                "city": "Laval",
            },
        )
        self.assertEqual(marketplace_response.status_code, 200)

        provider_cards = marketplace_response.context["providers"]
        selected_card = next(card for card in provider_cards if card.provider_id == provider.provider_id)
        self.assertEqual(selected_card.display_price, Decimal("100"))

        create_get_response = self.client.get(
            reverse("ui:request_create", args=[provider.provider_id]),
            {
                "service_type_id": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "service_timing": "urgent",
                "postal_code": "H7W4A2",
                "city": "Laval",
                "province": "QC",
            },
        )
        self.assertEqual(create_get_response.status_code, 200)

        preview = create_get_response.context["pricing"]
        self.assertEqual(preview["subtotal"], Decimal("100.00"))
        self.assertEqual(preview["tax"], Decimal("14.98"))
        self.assertEqual(preview["total"], Decimal("114.98"))
        self.assertEqual(preview["tax_region_code"], "QC")

        post_response = self.client.post(
            reverse("ui:request_create", args=[provider.provider_id]),
            {
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "service_timing": "urgent",
                "job_mode": Job.JobMode.ON_DEMAND,
            },
            follow=True,
        )
        self.assertEqual(post_response.status_code, 200)

        job = client.jobs.get()
        self.assertEqual(job.requested_subtotal_snapshot, Decimal("100.00"))
        self.assertEqual(job.requested_tax_snapshot, Decimal("14.98"))
        self.assertEqual(job.requested_total_snapshot, Decimal("114.98"))
        self.assertEqual(job.requested_tax_region_code_snapshot, "QC")

        status_response = self.client.get(reverse("ui:request_status", args=[job.job_id]))
        self.assertEqual(status_response.status_code, 200)
        self.assertContains(status_response, "100.00")
        self.assertContains(status_response, "14.98")
        self.assertContains(status_response, "114.98")
        self.assertContains(status_response, "Taxes (QC)")

    def test_request_create_get_uses_visible_service_timing_instead_of_job_mode_radios(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Timing",
            phone_number="5550000109",
            email="provider.timing@get.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="31 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Timing Request",
            description="Timing Request",
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Fast cleaning",
            billing_unit="fixed",
            price_cents=12500,
            is_active=True,
        )

        response = self.client.get(
            f"/request/{provider.provider_id}/?service_type_id={service_type.pk}&service_timing=urgent"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Service timing")
        self.assertContains(response, "Urgent")
        self.assertContains(response, 'type="hidden" name="service_timing" value="urgent"', html=False)
        self.assertContains(response, 'type="hidden" name="job_mode" value="on_demand"', html=False)
        self.assertNotContains(response, "On Demand (ASAP)")
        self.assertNotContains(response, "Scheduled date")

    def test_request_create_hides_authenticated_address_editor_for_emergency(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Emergency",
            phone_number="5550000110",
            email="provider.emergency.flow@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="32 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Emergency Address Flow",
            description="Emergency Address Flow",
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Emergency cleaning",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Emergency",
            phone_number="5550000111",
            email="client.emergency.flow@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="33 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.get(
            f"/request/{provider.provider_id}/?service_type_id={service_type.pk}&service_timing=emergency"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Service location: Laval, QC H7A0A1")
        self.assertContains(
            response,
            'type="hidden" id="postal_code" name="postal_code" value="H7A0A1"',
            html=False,
        )
        self.assertContains(
            response,
            'type="hidden" id="address_line1" name="address_line1" value="33 Client St"',
            html=False,
        )
        self.assertNotContains(response, "Use another service address")

    def test_request_create_hides_authenticated_address_editor_for_scheduled(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Scheduled Address",
            phone_number="5550000112",
            email="provider.scheduled.address@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="34 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Scheduled Address Flow",
            description="Scheduled Address Flow",
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Scheduled cleaning",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Scheduled",
            phone_number="5550000113",
            email="client.scheduled.address@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="35 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.get(
            f"/request/{provider.provider_id}/?service_type_id={service_type.pk}&service_timing=scheduled"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Service location: Laval, QC H7A0A1")
        self.assertContains(
            response,
            'type="hidden" id="postal_code" name="postal_code" value="H7A0A1"',
            html=False,
        )
        self.assertContains(
            response,
            'type="hidden" id="address_line1" name="address_line1" value="35 Client St"',
            html=False,
        )
        self.assertNotContains(response, "Use another service address")

    def test_request_create_hides_authenticated_address_editor_for_urgent(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Urgent Address",
            phone_number="5550000114",
            email="provider.urgent.address@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="36 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Urgent Address Flow",
            description="Urgent Address Flow",
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Urgent cleaning",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Urgent",
            phone_number="5550000115",
            email="client.urgent.address@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="37 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.get(
            f"/request/{provider.provider_id}/?service_type_id={service_type.pk}&service_timing=urgent"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Service location: Laval, QC H7A0A1")
        self.assertContains(
            response,
            'type="hidden" id="postal_code" name="postal_code" value="H7A0A1"',
            html=False,
        )
        self.assertContains(
            response,
            'type="hidden" id="address_line1" name="address_line1" value="37 Client St"',
            html=False,
        )
        self.assertNotContains(response, "Use another service address")

    def test_request_create_maps_scheduled_service_timing_to_scheduled_job_mode(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Scheduled",
            phone_number="5550000110",
            email="provider.scheduled.map@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="32 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Scheduled Mapping",
            description="Scheduled Mapping",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Scheduled cleaning",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Scheduled",
            phone_number="5550000111",
            email="client.scheduled.map@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="33 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "service_timing": "scheduled",
                "job_mode": Job.JobMode.ON_DEMAND,
                "scheduled_date": "2026-03-20",
                "scheduled_time": "14:30",
            },
        )

        self.assertEqual(response.status_code, 302)
        job = Job.objects.get()
        self.assertEqual(job.job_mode, Job.JobMode.SCHEDULED)
        self.assertFalse(job.is_asap)
        self.assertEqual(str(job.scheduled_date), "2026-03-20")
        self.assertEqual(job.scheduled_start_time.strftime("%H:%M"), "14:30")

    def test_request_create_maps_urgent_service_timing_to_on_demand_job_mode(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Urgent",
            phone_number="5550000112",
            email="provider.urgent.map@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="34 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Urgent Mapping",
            description="Urgent Mapping",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Urgent cleaning",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Urgent",
            phone_number="5550000113",
            email="client.urgent.map@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="35 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "service_timing": "urgent",
                "job_mode": Job.JobMode.SCHEDULED,
                "scheduled_date": "2026-03-20",
                "scheduled_time": "14:30",
            },
        )

        self.assertEqual(response.status_code, 302)
        job = Job.objects.get()
        self.assertEqual(job.job_mode, Job.JobMode.ON_DEMAND)
        self.assertTrue(job.is_asap)
        self.assertIsNone(job.scheduled_date)
        self.assertIsNone(job.scheduled_start_time)

    def test_authenticated_client_get_hides_manual_client_fields(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Session",
            phone_number="5550000000",
            email="provider.session@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="9 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Session Get Test",
            description="Session Get Test",
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Session Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Session",
            phone_number="5550000003",
            email="client.session@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="12 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.get(f"/request/{provider.provider_id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Logged in as Client Session")
        self.assertNotContains(response, "Client Information")

    def test_authenticated_client_get_shows_request_form_for_profile_postal_outside_service_area(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Coverage Get",
            phone_number="5550000028",
            email="provider.coverage.get@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="12 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Coverage Get Test",
            description="Coverage Get Test",
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Coverage Get Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Coverage Get",
            phone_number="5550000029",
            email="client.coverage.get@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H2X1A4",
            address_line1="13 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.get(f"/request/{provider.provider_id}/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "request/create.html")
        self.assertContains(
            response,
            "Service not available in this area. Please choose another address.",
        )
        self.assertContains(response, "Use another service address")
        self.assertContains(response, 'name="postal_code"', html=False)
        self.assertContains(response, 'value="H2X1A4"', html=False)

    def test_providers_nearby_shows_top_ranked_providers_for_fsa_and_service_type(self):
        service_type = ServiceType.objects.create(
            name="Alternative Coverage Test",
            description="Alternative Coverage Test",
        )
        alternative_names = [
            "CleanPro Montreal",
            "ABC Cleaning",
            "Sparkle Services",
            "Fourth Option",
        ]
        alternatives = []
        for index, name in enumerate(alternative_names, start=1):
            alternative_provider = Provider.objects.create(
                provider_type="company",
                company_name=name,
                contact_first_name=f"Alt{index}",
                contact_last_name="Provider",
                phone_number=f"555000013{index}",
                email=f"provider.nearby.{index}@test.local",
                province="QC",
                city="Montreal",
                postal_code="H2X1A4",
                address_line1=f"{30 + index} Alternative St",
                avg_rating=Decimal(f"{5 - (index / 10):.2f}"),
            )
            ProviderServiceArea.objects.create(
                provider=alternative_provider,
                city="Montreal",
                province="QC",
                postal_prefix="H2X",
                is_active=True,
            )
            ProviderService.objects.create(
                provider=alternative_provider,
                service_type=service_type,
                custom_name=f"{name} Service",
                billing_unit="fixed",
                price_cents=12000 + index,
                is_active=True,
            )
            alternatives.append(alternative_provider)

        different_service_provider = Provider.objects.create(
            provider_type="company",
            company_name="Wrong Service Provider",
            contact_first_name="Wrong",
            contact_last_name="Service",
            phone_number="5550000039",
            email="provider.alt.get.wrong-service@test.local",
            province="QC",
            city="Montreal",
            postal_code="H2X1A4",
            address_line1="39 Wrong Service St",
        )
        ProviderServiceArea.objects.create(
            provider=different_service_provider,
            city="Montreal",
            province="QC",
            postal_prefix="H2X",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=different_service_provider,
            service_type=ServiceType.objects.create(
                name="Different Alternative Service",
                description="Different Alternative Service",
            ),
            custom_name="Wrong Service",
            billing_unit="fixed",
            price_cents=9000,
            is_active=True,
        )

        other_area_provider = Provider.objects.create(
            provider_type="company",
            company_name="Other Area Provider",
            contact_first_name="Other",
            contact_last_name="Area",
            phone_number="5550000040",
            email="provider.alt.get.other-area@test.local",
            province="QC",
            city="Quebec",
            postal_code="G1A0A2",
            address_line1="40 Other Area St",
        )
        ProviderServiceArea.objects.create(
            provider=other_area_provider,
            city="Quebec",
            province="QC",
            postal_prefix="G1A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=other_area_provider,
            service_type=service_type,
            custom_name="Other Area Service",
            billing_unit="fixed",
            price_cents=11000,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:providers_nearby"),
            {
                "fsa": "H2X",
                "service_type": str(service_type.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Providers available in your area")
        for alternative_provider in alternatives:
            self.assertContains(response, alternative_provider.company_name)
            self.assertContains(
                response,
                f'href="{reverse("ui:request_create", args=[alternative_provider.provider_id])}?service_type_id={service_type.pk}"',
                html=False,
            )
        self.assertNotContains(response, "Wrong Service Provider")
        self.assertNotContains(response, "Other Area Provider")
        response_text = response.content.decode()
        self.assertLess(
            response_text.index("CleanPro Montreal"),
            response_text.index("ABC Cleaning"),
        )
        self.assertLess(
            response_text.index("ABC Cleaning"),
            response_text.index("Sparkle Services"),
        )
        self.assertLess(
            response_text.index("Sparkle Services"),
            response_text.index("Fourth Option"),
        )

    def test_providers_nearby_allows_change_address_with_postal_code_and_preserves_view_link_params(self):
        service_type = ServiceType.objects.create(
            name="Nearby Change Address Test",
            description="Nearby Change Address Test",
        )
        provider = Provider.objects.create(
            provider_type="company",
            company_name="Postal Area Cleaning",
            contact_first_name="Postal",
            contact_last_name="Cleaner",
            phone_number="5550000140",
            email="provider.nearby.postal@test.local",
            province="QC",
            city="Montreal",
            postal_code="H2X1A4",
            address_line1="60 Postal St",
            avg_rating=Decimal("4.90"),
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Montreal",
            province="QC",
            postal_prefix="H2X",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Postal Area Service",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:providers_nearby"),
            {
                "postal_code": "H2X1A4",
                "city": "Montreal",
                "province": "QC",
                "service_type": str(service_type.pk),
                "service_timing": "emergency",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Change address")
        self.assertContains(response, 'name="postal_code"', html=False)
        self.assertContains(response, 'value="H2X1A4"', html=False)
        self.assertContains(response, "Postal code / area")
        self.assertContains(response, "Update results")
        self.assertContains(
            response,
            (
                f'href="{reverse("ui:request_create", args=[provider.provider_id])}'
                f'?service_type_id={service_type.pk}'
                f'&amp;postal_code=H2X1A4'
                f'&amp;city=Montreal'
                f'&amp;province=QC'
                f'&amp;service_timing=emergency"'
            ),
            html=False,
        )

    def test_providers_nearby_preserves_search_in_request_link(self):
        service_type = ServiceType.objects.create(
            name="Nearby Search Preserve Test",
            description="Nearby Search Preserve Test",
        )
        provider = Provider.objects.create(
            provider_type="company",
            company_name="Provider Manual Test",
            contact_first_name="Provider",
            contact_last_name="Manual",
            phone_number="5550000141",
            email="provider.nearby.search-link@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="61 Search St",
            avg_rating=Decimal("4.90"),
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Manual Search Service",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:providers_nearby"),
            {
                "search": "manual",
                "postal_code": "H7A",
                "city": "Laval",
                "province": "QC",
                "service_type": str(service_type.pk),
                "service_timing": "emergency",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="search"', html=False)
        self.assertContains(response, 'value="manual"', html=False)
        self.assertContains(
            response,
            (
                f'href="{reverse("ui:request_create", args=[provider.provider_id])}'
                f'?service_type_id={service_type.pk}'
                f'&amp;postal_code=H7A'
                f'&amp;city=Laval'
                f'&amp;province=QC'
                f'&amp;search=manual'
                f'&amp;service_timing=emergency"'
            ),
            html=False,
        )

    def test_marketplace_to_request_location_contract(self):
        response = self.client.get(
            reverse("ui:providers_nearby"),
            {
                "postal_code": "H7A",
                "city": "Laval",
                "province": "QC",
                "service_timing": "emergency",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laval")

    def test_request_create_receives_location_from_nearby(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Nearby Contract",
            phone_number="5550000141",
            email="provider.nearby.contract@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="61 Nearby St",
        )
        service_type = ServiceType.objects.create(
            name="Nearby Contract Test",
            description="Nearby Contract Test",
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Nearby Contract Service",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:request_create", args=[provider.provider_id]),
            {
                "service_type_id": str(service_type.pk),
                "postal_code": "H7A",
                "city": "Laval",
                "province": "QC",
                "search": "manual",
                "service_timing": "emergency",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form_data"]["postal_code"], "H7A")
        self.assertEqual(response.context["form_data"]["city"], "Laval")
        self.assertEqual(response.context["form_data"]["province"], "QC")
        self.assertEqual(response.context["search"], "manual")
        self.assertContains(response, 'name="city" required value="Laval"', html=False)
        self.assertContains(response, 'type="hidden" name="search" value="manual"', html=False)

    def test_request_create_preserves_provider_name_context(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Provider Name",
            phone_number="5550000142",
            email="provider.request.provider-name@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="62 Nearby St",
        )
        service_type = ServiceType.objects.create(
            name="Provider Name Context Test",
            description="Provider Name Context Test",
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Provider Name Context Service",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:request_create", args=[provider.provider_id]),
            {
                "service_type_id": str(service_type.pk),
                "provider_name": "manual",
                "search": "deep",
                "postal_code": "H7A",
                "city": "Laval",
                "province": "QC",
                "service_timing": "emergency",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["search"], "deep")
        self.assertEqual(response.context["provider_name"], "manual")
        self.assertEqual(response.context["postal_code"], "H7A")
        self.assertEqual(response.context["city"], "Laval")
        self.assertEqual(response.context["province"], "QC")
        self.assertEqual(response.context["service_timing"], "emergency")
        self.assertContains(
            response,
            'type="hidden" name="provider_name" value="manual"',
            html=False,
        )

    def test_providers_nearby_job_orders_covered_providers_by_hybrid_score(self):
        client = Client.objects.create(
            first_name="Nearby",
            last_name="Client",
            phone_number="5550000490",
            email="nearby.job.client@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="1 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        service_type = ServiceType.objects.create(
            name="Nearby Job Service",
            description="Nearby Job Service",
        )
        job = Job.objects.create(
            client=client,
            service_type=service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="1 Client St",
        )
        JobLocation.objects.create(
            job=job,
            latitude=Decimal("45.560100"),
            longitude=Decimal("-73.712400"),
            postal_code="H7A1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )

        close_provider = Provider.objects.create(
            provider_type="company",
            company_name="Close Provider",
            contact_first_name="Close",
            contact_last_name="Provider",
            phone_number="5550000491",
            email="provider.nearby.job.close@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="10 Close St",
            is_active=True,
            avg_rating=Decimal("1.00"),
        )
        ProviderServiceArea.objects.create(
            provider=close_provider,
            city="Laval",
            province="QC",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=close_provider,
            service_type=service_type,
            custom_name="Close Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        ProviderLocation.objects.create(
            provider=close_provider,
            latitude=Decimal("45.561000"),
            longitude=Decimal("-73.713000"),
            postal_code="H7A1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )

        far_provider = Provider.objects.create(
            provider_type="company",
            company_name="Far Provider",
            contact_first_name="Far",
            contact_last_name="Provider",
            phone_number="5550000492",
            email="provider.nearby.job.far@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="20 Far St",
            is_active=True,
            avg_rating=Decimal("5.00"),
        )
        ProviderServiceArea.objects.create(
            provider=far_provider,
            city="Laval",
            province="QC",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=far_provider,
            service_type=service_type,
            custom_name="Far Service",
            billing_unit="fixed",
            price_cents=9000,
            is_active=True,
        )
        ProviderLocation.objects.create(
            provider=far_provider,
            latitude=Decimal("45.501700"),
            longitude=Decimal("-73.567300"),
            postal_code="H1A1A1",
            city="Montreal",
            province="QC",
            country="Canada",
        )

        no_location_provider = Provider.objects.create(
            provider_type="company",
            company_name="No Location Provider",
            contact_first_name="No",
            contact_last_name="Location",
            phone_number="5550000493",
            email="provider.nearby.job.nolocation@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="30 No Location St",
            is_active=True,
        )
        ProviderServiceArea.objects.create(
            provider=no_location_provider,
            city="Laval",
            province="QC",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=no_location_provider,
            service_type=service_type,
            custom_name="No Location Service",
            billing_unit="fixed",
            price_cents=11000,
            is_active=True,
        )

        outside_coverage_provider = Provider.objects.create(
            provider_type="company",
            company_name="Outside Coverage Provider",
            contact_first_name="Outside",
            contact_last_name="Coverage",
            phone_number="5550000494",
            email="provider.nearby.job.outside@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="40 Outside St",
            is_active=True,
        )
        ProviderServiceArea.objects.create(
            provider=outside_coverage_provider,
            city="Montreal",
            province="QC",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=outside_coverage_provider,
            service_type=service_type,
            custom_name="Outside Service",
            billing_unit="fixed",
            price_cents=9500,
            is_active=True,
        )
        ProviderLocation.objects.create(
            provider=outside_coverage_provider,
            latitude=Decimal("45.503000"),
            longitude=Decimal("-73.570000"),
            postal_code="H1A1A1",
            city="Montreal",
            province="QC",
            country="Canada",
        )

        response = self.client.get(reverse("ui:providers_nearby_job", args=[job.job_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Providers near this job")
        self.assertContains(response, "Close Provider")
        self.assertContains(response, "Far Provider")
        self.assertNotContains(response, "No Location Provider")
        self.assertNotContains(response, "Outside Coverage Provider")
        self.assertContains(response, "Distance:")
        self.assertContains(response, "Score:")
        response_text = response.content.decode()
        self.assertLess(
            response_text.index("Far Provider"),
            response_text.index("Close Provider"),
        )

    def test_providers_nearby_job_fairness_can_outrank_recently_assigned_provider(self):
        client = Client.objects.create(
            first_name="Fair",
            last_name="Client",
            phone_number="5550000496",
            email="nearby.job.fair@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="3 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        service_type = ServiceType.objects.create(
            name="Nearby Fairness Service",
            description="Nearby Fairness Service",
        )
        job = Job.objects.create(
            client=client,
            service_type=service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="3 Client St",
        )
        JobLocation.objects.create(
            job=job,
            latitude=Decimal("45.560100"),
            longitude=Decimal("-73.712400"),
            postal_code="H7A1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )

        now = timezone.now()
        recent_provider = Provider.objects.create(
            provider_type="company",
            company_name="Recent Provider",
            contact_first_name="Recent",
            contact_last_name="Provider",
            phone_number="5550000497",
            email="provider.nearby.job.recent@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="11 Recent St",
            is_active=True,
            avg_rating=Decimal("5.00"),
            last_job_assigned_at=now,
        )
        ProviderServiceArea.objects.create(
            provider=recent_provider,
            city="Laval",
            province="QC",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=recent_provider,
            service_type=service_type,
            custom_name="Recent Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        ProviderLocation.objects.create(
            provider=recent_provider,
            latitude=Decimal("45.560100"),
            longitude=Decimal("-73.712400"),
            postal_code="H7A1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )

        rested_provider = Provider.objects.create(
            provider_type="company",
            company_name="Rested Provider",
            contact_first_name="Rested",
            contact_last_name="Provider",
            phone_number="5550000498",
            email="provider.nearby.job.rested@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="22 Rested St",
            is_active=True,
            avg_rating=Decimal("5.00"),
            last_job_assigned_at=now - timedelta(hours=4),
        )
        ProviderServiceArea.objects.create(
            provider=rested_provider,
            city="Laval",
            province="QC",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=rested_provider,
            service_type=service_type,
            custom_name="Rested Service",
            billing_unit="fixed",
            price_cents=9900,
            is_active=True,
        )
        ProviderLocation.objects.create(
            provider=rested_provider,
            latitude=Decimal("45.515000"),
            longitude=Decimal("-73.620000"),
            postal_code="H1A1A1",
            city="Montreal",
            province="QC",
            country="Canada",
        )

        response = self.client.get(reverse("ui:providers_nearby_job", args=[job.job_id]))

        self.assertEqual(response.status_code, 200)
        response_text = response.content.decode()
        self.assertLess(
            response_text.index("Rested Provider"),
            response_text.index("Recent Provider"),
        )

    def test_providers_nearby_job_shows_error_when_job_has_no_location(self):
        client = Client.objects.create(
            first_name="No",
            last_name="Location",
            phone_number="5550000495",
            email="nearby.job.nolocation@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="2 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        service_type = ServiceType.objects.create(
            name="No Location Job Service",
            description="No Location Job Service",
        )
        job = Job.objects.create(
            client=client,
            service_type=service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="2 Client St",
        )

        response = self.client.get(reverse("ui:providers_nearby_job", args=[job.job_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Job has no location.")

    def test_authenticated_client_can_create_job_without_manual_client_fields(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Session",
            phone_number="5550000004",
            email="provider.session.create@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="13 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Session Create Test",
            description="Session Create Test",
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Session Create Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Session",
            phone_number="5550000005",
            email="client.session.create@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="14 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "job_mode": "on_demand",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.jobs.count(), 1)
        job = client.jobs.first()
        self.assertEqual(job.selected_provider_id, provider.pk)
        self.assertEqual(job.provider_service_name_snapshot, "Session Create Service")
        self.assertEqual(job.requested_subservice_name, "")
        self.assertEqual(job.requested_quantity_snapshot, Decimal("1.00"))
        self.assertEqual(job.requested_unit_price_snapshot, Decimal("100.00"))
        self.assertEqual(job.requested_billing_unit_snapshot, "fixed")
        self.assertEqual(job.requested_base_line_total_snapshot, Decimal("100.00"))
        self.assertEqual(job.requested_subservice_base_price_snapshot, Decimal("100.00"))
        self.assertEqual(job.requested_subtotal_snapshot, Decimal("100.00"))
        self.assertEqual(job.requested_tax_snapshot, Decimal("14.98"))
        self.assertEqual(job.requested_tax_rate_bps_snapshot, 14975)
        self.assertEqual(job.requested_tax_region_code_snapshot, "QC")
        self.assertEqual(job.requested_total_snapshot, Decimal("114.98"))

    def test_job_price_snapshot_does_not_change_when_provider_price_changes(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Price Lock",
            phone_number="5550000006",
            email="provider.price.lock@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="15 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Price Lock Test",
            description="Price Lock Test",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Locked Price Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Price Lock",
            phone_number="5550000007",
            email="client.price.lock@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="16 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "job_mode": "on_demand",
            },
        )

        self.assertEqual(response.status_code, 302)
        job = client.jobs.get()
        self.assertEqual(job.requested_unit_price_snapshot, Decimal("100.00"))
        self.assertEqual(job.requested_subtotal_snapshot, Decimal("100.00"))
        self.assertEqual(job.requested_tax_snapshot, Decimal("14.98"))
        self.assertEqual(job.requested_total_snapshot, Decimal("114.98"))

        provider_service.price_cents = 20000
        provider_service.save(update_fields=["price_cents"])

        job.refresh_from_db()

        self.assertEqual(job.requested_unit_price_snapshot, Decimal("100.00"))
        self.assertEqual(job.requested_subtotal_snapshot, Decimal("100.00"))
        self.assertEqual(job.requested_tax_snapshot, Decimal("14.98"))
        self.assertEqual(job.requested_total_snapshot, Decimal("114.98"))

    def test_request_create_redirects_to_job_created_page_without_success_message(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Message",
            phone_number="5550000010",
            email="provider.message@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="19 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Message Service",
            description="Message Service",
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Message Offer",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Message",
            phone_number="5550000011",
            email="client.message@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="20 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "job_mode": "on_demand",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        job = client.jobs.get()
        job_created_url = reverse("ui:job_created", args=[job.job_id])
        self.assertEqual(response.request["PATH_INFO"], job_created_url)
        self.assertEqual(response.request["QUERY_STRING"], "service_timing=urgent")
        self.assertNotContains(response, "Request created")
        self.assertContains(response, "Job created")
        self.assertContains(response, "Job ID")
        self.assertContains(response, f">{job.job_id}<", html=False)
        self.assertContains(response, "Waiting for provider response")
        self.assertContains(response, "Price snapshot")
        self.assertContains(response, "Timing")
        self.assertContains(response, "Urgent")
        self.assertContains(response, "Next step")
        self.assertContains(response, "Pending confirmation")
        self.assertContains(
            response,
            f'{reverse("ui:request_status", args=[job.job_id])}?service_timing=urgent',
        )
        created_event = job.events.get(event_type=JobEvent.EventType.JOB_CREATED)
        waiting_event = job.events.get(event_type=JobEvent.EventType.WAITING_PROVIDER_RESPONSE)
        self.assertEqual(created_event.actor_role, JobEvent.ActorRole.CLIENT)
        self.assertEqual(created_event.visible_status, "Waiting for provider response")
        self.assertEqual(created_event.payload_json.get("service_timing"), "urgent")
        self.assertEqual(waiting_event.actor_role, JobEvent.ActorRole.SYSTEM)
        self.assertEqual(waiting_event.visible_status, "Waiting for provider response")

    def test_job_created_view_shows_request_summary(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Created View",
            phone_number="5550000012",
            email="provider.created.view@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="21 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Created View Test",
            description="Created View Test",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Created View Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Created View",
            phone_number="5550000013",
            email="client.created.view@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="22 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        job = Job.objects.create(
            selected_provider=provider,
            provider_service=provider_service,
            client=client,
            service_type=service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="22 Client St",
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            provider_service_name_snapshot="Created View Service",
            requested_quantity_snapshot=Decimal("1.00"),
            requested_base_line_total_snapshot=Decimal("100.00"),
            requested_subtotal_snapshot=Decimal("100.00"),
            requested_tax_snapshot=Decimal("14.98"),
            requested_tax_rate_bps_snapshot=14975,
            requested_tax_region_code_snapshot="QC",
            requested_total_snapshot=Decimal("114.98"),
            requested_billing_unit_snapshot="fixed",
        )

        response = self.client.get(
            reverse("ui:job_created", args=[job.job_id]),
            {"service_timing": "emergency"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Job created")
        self.assertContains(response, "Created View Service")
        self.assertContains(response, "Created View Test")
        self.assertContains(response, "22 Client St")
        self.assertContains(response, "Laval, QC H7A0A1")
        self.assertContains(response, "Waiting for provider response")
        self.assertContains(response, f">{job.job_id}<", html=False)
        self.assertContains(response, "$100.00")
        self.assertContains(response, "$14.98")
        self.assertContains(response, "$114.98")
        self.assertContains(response, "Emergency")
        self.assertContains(response, "Waiting for provider response")
        self.assertEqual(response.context["service_timing"], "emergency")
        self.assertEqual(response.context["next_step_label"], "Waiting for provider response")
        self.assertContains(
            response,
            f'{reverse("ui:request_status", args=[job.job_id])}?service_timing=emergency',
        )

    def test_job_created_view_falls_back_to_scheduled_timing_from_job_mode(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Scheduled Created View",
            phone_number="5550000014",
            email="provider.created.scheduled@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="23 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Scheduled Created View Test",
            description="Scheduled Created View Test",
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Scheduled Created View",
            phone_number="5550000015",
            email="client.created.scheduled@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="24 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        job = Job.objects.create(
            selected_provider=provider,
            client=client,
            service_type=service_type,
            job_mode=Job.JobMode.SCHEDULED,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=2),
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="24 Client St",
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
        )

        response = self.client.get(reverse("ui:job_created", args=[job.job_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Scheduled")
        self.assertContains(response, "Request submitted")
        self.assertEqual(response.context["service_timing"], "scheduled")
        self.assertEqual(response.context["next_step_label"], "Request submitted")

    def test_request_create_allows_matching_geocoded_province(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Geo Match",
            phone_number="5550000030",
            email="provider.geo.match@test.local",
            province="NS",
            city="Halifax",
            postal_code="B6P 1B3",
            address_line1="35 Provider St",
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Halifax",
            province="NS",
            is_active=True,
        )
        service_type = ServiceType.objects.create(
            name="Geo Match Service",
            description="Geo Match Service",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Geo Match Offer",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Geo Match",
            phone_number="5550000031",
            email="client.geo.match@test.local",
            country="Canada",
            province="NS",
            city="Halifax",
            postal_code="B6P 1B3",
            address_line1="36 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        self.geocode_address_mock.return_value = {
            "lat": 44.6508608,
            "lng": -63.5923256,
            "components": [
                {
                    "short_name": "NS",
                    "types": ["administrative_area_level_1", "political"],
                }
            ]
        }

        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "job_mode": "on_demand",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(JobLocation.objects.count(), 1)
        self.assertEqual(JobLocation.objects.get().city, "Halifax")
        self.geocode_address_mock.assert_called_once_with(
            "B6P 1B3",
            city="Halifax",
            province="NS",
        )

    def test_request_create_blocks_when_geocoded_province_mismatches(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Geo Mismatch",
            phone_number="5550000032",
            email="provider.geo.mismatch@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="37 Provider St",
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            is_active=True,
        )
        service_type = ServiceType.objects.create(
            name="Geo Mismatch Service",
            description="Geo Mismatch Service",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Geo Mismatch Offer",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Geo Mismatch",
            phone_number="5550000033",
            email="client.geo.mismatch@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="38 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        self.geocode_address_mock.return_value = {
            "components": [
                {
                    "short_name": "NS",
                    "types": ["administrative_area_level_1", "political"],
                }
            ]
        }

        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "job_mode": "on_demand",
                "use_other_address": "1",
                "province": "QC",
                "city": "Laval",
                "postal_code": "B6P 1B3",
                "address_line1": "99 Remote St",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "The postal code does not belong to the selected province.",
        )
        self.assertEqual(Job.objects.count(), 0)
        self.assertEqual(JobLocation.objects.count(), 0)
        self.geocode_address_mock.assert_called_once_with(
            "B6P 1B3",
            city="Laval",
            province="QC",
        )

    def test_request_create_redirects_authenticated_client_other_address_outside_service_area(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Coverage",
            phone_number="5550000006",
            email="provider.coverage@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="15 Provider St",
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        service_type = ServiceType.objects.create(
            name="Coverage Test",
            description="Coverage Test",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Coverage Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Coverage",
            phone_number="5550000007",
            email="client.coverage@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="16 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "job_mode": "on_demand",
                "use_other_address": "1",
                "province": "QC",
                "city": "Montreal",
                "postal_code": "H2X1A4",
                "address_line1": "99 Remote St",
            },
        )

        self.assertRedirects(
            response,
            (
                f"{reverse('ui:providers_nearby')}"
                f"?fsa=H2X&postal_code=H2X1A4&city=Montreal&province=QC"
                f"&service_type={service_type.pk}"
            ),
        )
        self.assertEqual(Job.objects.count(), 0)

    def test_providers_nearby_filters_by_rating_and_search(self):
        service_type = ServiceType.objects.create(
            name="Nearby Filters Test",
            description="Nearby Filters Test",
        )
        matching_provider = Provider.objects.create(
            provider_type="company",
            company_name="Neighbourhood Cleaners",
            contact_first_name="Neighbourhood",
            contact_last_name="Team",
            phone_number="5550000041",
            email="provider.nearby.filters.match@test.local",
            province="QC",
            city="Montreal",
            postal_code="H2X1A4",
            address_line1="41 Provider St",
            avg_rating=Decimal("4.90"),
        )
        ProviderServiceArea.objects.create(
            provider=matching_provider,
            city="Montreal",
            province="QC",
            postal_prefix="H2X",
            is_active=True,
        )
        matching_offer = ProviderService.objects.create(
            provider=matching_provider,
            service_type=service_type,
            custom_name="Neighbourhood Coverage Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        ProviderServiceSubservice.objects.create(
            provider_service=matching_offer,
            name="Deep Cleaning",
            base_price=Decimal("150.00"),
            is_active=True,
            sort_order=1,
        )
        low_rating_provider = Provider.objects.create(
            provider_type="company",
            company_name="Budget Clean Team",
            contact_first_name="Budget",
            contact_last_name="Team",
            phone_number="5550000042",
            email="provider.nearby.filters.low@test.local",
            province="QC",
            city="Montreal",
            postal_code="H2X1A4",
            address_line1="42 Provider St",
            avg_rating=Decimal("4.20"),
        )
        ProviderServiceArea.objects.create(
            provider=low_rating_provider,
            city="Montreal",
            province="QC",
            postal_prefix="H2X",
            is_active=True,
        )
        low_rating_offer = ProviderService.objects.create(
            provider=low_rating_provider,
            service_type=service_type,
            custom_name="Budget Coverage Service",
            billing_unit="fixed",
            price_cents=9000,
            is_active=True,
        )
        ProviderServiceSubservice.objects.create(
            provider_service=low_rating_offer,
            name="Deep Cleaning",
            base_price=Decimal("140.00"),
            is_active=True,
            sort_order=1,
        )
        other_name_provider = Provider.objects.create(
            provider_type="company",
            company_name="Sparkle Services",
            contact_first_name="Sparkle",
            contact_last_name="Team",
            phone_number="5550000043",
            email="provider.nearby.filters.other@test.local",
            province="QC",
            city="Montreal",
            postal_code="H2X1A4",
            address_line1="43 Alternative St",
            avg_rating=Decimal("4.80"),
        )
        ProviderServiceArea.objects.create(
            provider=other_name_provider,
            city="Montreal",
            province="QC",
            postal_prefix="H2X",
            is_active=True,
        )
        other_name_offer = ProviderService.objects.create(
            provider=other_name_provider,
            service_type=service_type,
            custom_name="Sparkle Coverage Service",
            billing_unit="fixed",
            price_cents=9500,
            is_active=True,
        )
        ProviderServiceExtra.objects.create(
            provider_service=other_name_offer,
            name="Inside Fridge",
            unit_price=Decimal("15.00"),
            is_active=True,
            min_qty=1,
            max_qty=3,
            sort_order=1,
        )

        response = self.client.get(
            reverse("ui:providers_nearby"),
            {
                "fsa": "H2X",
                "service_type": str(service_type.pk),
                "rating": "4.5",
                "search": "deep",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Neighbourhood Cleaners")
        self.assertNotContains(response, "Budget Clean Team")
        self.assertNotContains(response, "Sparkle Services")
        self.assertContains(response, "Search provider, service or extra")

    def test_providers_nearby_search_matches_provider_name(self):
        service_type = ServiceType.objects.create(
            name="Nearby Provider Name Search Test",
            description="Nearby Provider Name Search Test",
        )
        matching_provider = Provider.objects.create(
            provider_type="company",
            company_name="Provider Manual Test",
            contact_first_name="Provider",
            contact_last_name="Manual",
            phone_number="5550000044",
            email="provider.nearby.name.match@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="44 Provider St",
            avg_rating=Decimal("4.90"),
        )
        ProviderServiceArea.objects.create(
            provider=matching_provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=matching_provider,
            service_type=service_type,
            custom_name="Manual Coverage Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )

        other_provider = Provider.objects.create(
            provider_type="company",
            company_name="Other Nearby Team",
            contact_first_name="Other",
            contact_last_name="Nearby",
            phone_number="5550000045",
            email="provider.nearby.name.other@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="45 Provider St",
            avg_rating=Decimal("4.80"),
        )
        ProviderServiceArea.objects.create(
            provider=other_provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=other_provider,
            service_type=service_type,
            custom_name="Other Coverage Service",
            billing_unit="fixed",
            price_cents=9500,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:providers_nearby"),
            {
                "search": "manual",
                "postal_code": "H7A",
                "service_type": str(service_type.pk),
                "service_timing": "emergency",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provider Manual Test")
        self.assertNotContains(response, "Other Nearby Team")
        self.assertContains(response, "Search provider, service or extra")

    def test_providers_nearby_deduplicates_matching_offers_per_provider(self):
        service_type = ServiceType.objects.create(
            name="Nearby Dedupe Test",
            description="Nearby Dedupe Test",
        )
        provider = Provider.objects.create(
            provider_type="company",
            company_name="Single Card Cleaning",
            contact_first_name="Single",
            contact_last_name="Card",
            phone_number="5550000044",
            email="provider.nearby.dedupe@test.local",
            province="QC",
            city="Montreal",
            postal_code="H2X1A4",
            address_line1="44 Provider St",
            avg_rating=Decimal("4.90"),
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Montreal",
            province="QC",
            postal_prefix="H2X",
            is_active=True,
        )
        first_offer = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Single Card Base",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        second_offer = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Single Card Premium",
            billing_unit="fixed",
            price_cents=11000,
            is_active=True,
        )
        ProviderServiceSubservice.objects.create(
            provider_service=first_offer,
            name="Deep Cleaning",
            base_price=Decimal("140.00"),
            is_active=True,
            sort_order=1,
        )
        ProviderServiceExtra.objects.create(
            provider_service=second_offer,
            name="Deep Sanitizing",
            unit_price=Decimal("20.00"),
            is_active=True,
            min_qty=1,
            max_qty=3,
            sort_order=1,
        )

        response = self.client.get(
            reverse("ui:providers_nearby"),
            {
                "fsa": "H2X",
                "service_type": str(service_type.pk),
                "search": "deep",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["providers"]), 1)
        self.assertEqual(response.context["providers"][0].provider_id, provider.pk)
        self.assertContains(response, "Single Card Cleaning")

    def test_request_create_accepts_authenticated_client_other_address_matching_postal_prefix(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="FSA Coverage",
            phone_number="5550000008",
            email="provider.fsa.coverage@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="17 Provider St",
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        service_type = ServiceType.objects.create(
            name="FSA Coverage Test",
            description="FSA Coverage Test",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="FSA Coverage Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="FSA Coverage",
            phone_number="5550000009",
            email="client.fsa.coverage@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="18 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "job_mode": "on_demand",
                "use_other_address": "1",
                "province": "QC",
                "city": "Montreal",
                "postal_code": "h7a 9z9",
                "address_line1": "100 FSA Match St",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Job.objects.count(), 1)
        job = Job.objects.get()
        self.assertEqual(job.city, "Montreal")
        self.assertEqual(job.postal_code, "h7a 9z9")

    def test_request_create_authenticated_client_other_address_uses_posted_province_for_tax_snapshot(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Tax Province",
            phone_number="5550000010",
            email="provider.tax.province@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="19 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Tax Province Test",
            description="Tax Province Test",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Province-aware pricing",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Tax Province",
            phone_number="5550000011",
            email="client.tax.province@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="20 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "job_mode": "on_demand",
                "use_other_address": "1",
                "province": "ON",
                "city": "Toronto",
                "postal_code": "M5V1E3",
                "address_line1": "21 Other Province St",
            },
        )

        self.assertEqual(response.status_code, 302)
        job = Job.objects.get()
        self.assertEqual(job.province, "ON")
        self.assertEqual(job.requested_tax_region_code_snapshot, "ON")
        self.assertEqual(job.requested_tax_rate_bps_snapshot, 13000)
        self.assertEqual(job.requested_tax_snapshot, Decimal("13.00"))
        self.assertEqual(job.requested_total_snapshot, Decimal("113.00"))

    def test_job_snapshot_preserves_request_location(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Location Snapshot",
            phone_number="5550000012",
            email="provider.location.snapshot@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="22 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Location Snapshot Test",
            description="Location Snapshot Test",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Location Snapshot Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Location Snapshot",
            phone_number="5550000013",
            email="client.location.snapshot@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="23 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            reverse("ui:request_create", args=[provider.provider_id]),
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "postal_code": "H7A",
                "city": "Laval",
                "province": "QC",
                "address_line1": "23 Client St",
                "service_timing": "scheduled",
                "job_mode": Job.JobMode.ON_DEMAND,
                "scheduled_date": str(timezone.localdate() + timedelta(days=7)),
                "scheduled_time": "14:30",
            },
        )

        self.assertEqual(response.status_code, 302)
        job = Job.objects.get()
        self.assertEqual(job.postal_code, "H7A")
        self.assertEqual(job.city, "Laval")
        self.assertEqual(job.province, "QC")

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
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Request Create Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
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
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Profile Gate Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
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

    def test_request_create_saves_requested_subservice_and_extras_snapshots(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Catalog",
            phone_number="5550000013",
            email="provider.catalog@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="22 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Catalog Service",
            description="Catalog Service",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Catalog Offer",
            billing_unit="fixed",
            price_cents=12500,
            is_active=True,
        )
        deep = ProviderServiceSubservice.objects.create(
            provider_service=provider_service,
            name="Deep Cleaning",
            base_price=Decimal("150.00"),
            is_active=True,
            sort_order=1,
        )
        extra_bathroom = ProviderServiceExtra.objects.create(
            provider_service=provider_service,
            name="Extra bathroom",
            unit_price=Decimal("25.00"),
            is_active=True,
            min_qty=1,
            max_qty=5,
            sort_order=1,
        )
        inside_fridge = ProviderServiceExtra.objects.create(
            provider_service=provider_service,
            name="Inside fridge",
            unit_price=Decimal("15.00"),
            is_active=True,
            min_qty=1,
            max_qty=3,
            sort_order=2,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Catalog",
            phone_number="5550000014",
            email="client.catalog@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="23 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "requested_quantity": "2",
                "requested_subservice_id": str(deep.pk),
                "selected_extras": [str(extra_bathroom.pk), str(inside_fridge.pk)],
                f"extra_qty_{extra_bathroom.pk}": "2",
                f"extra_qty_{inside_fridge.pk}": "",
                "job_mode": "on_demand",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Job.objects.count(), 1)

        job = Job.objects.get()
        requested_extras = list(job.requested_extras.order_by("extra_name_snapshot"))

        self.assertRedirects(
            response,
            reverse("ui:job_created", args=[job.job_id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(job.selected_provider_id, provider.pk)
        self.assertEqual(job.provider_service_id, provider_service.pk)
        self.assertEqual(job.provider_service_name_snapshot, "Catalog Offer")
        self.assertEqual(job.requested_subservice_name, "Deep Cleaning")
        self.assertEqual(job.requested_subservice_id_snapshot, deep.pk)
        self.assertEqual(job.requested_quantity_snapshot, Decimal("2.00"))
        self.assertEqual(job.requested_unit_price_snapshot, Decimal("150.00"))
        self.assertEqual(job.requested_billing_unit_snapshot, "fixed")
        self.assertEqual(job.requested_base_line_total_snapshot, Decimal("300.00"))
        self.assertEqual(job.requested_subservice_base_price_snapshot, Decimal("150.00"))
        self.assertEqual(job.requested_subtotal_snapshot, Decimal("365.00"))
        self.assertEqual(job.requested_tax_snapshot, Decimal("54.66"))
        self.assertEqual(job.requested_tax_rate_bps_snapshot, 14975)
        self.assertEqual(job.requested_tax_region_code_snapshot, "QC")
        self.assertEqual(job.requested_total_snapshot, Decimal("419.66"))
        self.assertEqual(len(requested_extras), 2)
        self.assertEqual(requested_extras[0].extra_name_snapshot, "Extra bathroom")
        self.assertEqual(requested_extras[0].quantity, 2)
        self.assertEqual(requested_extras[0].unit_price_snapshot, Decimal("25.00"))
        self.assertEqual(requested_extras[0].line_total_snapshot, Decimal("50.00"))
        self.assertEqual(requested_extras[1].extra_name_snapshot, "Inside fridge")
        self.assertEqual(requested_extras[1].quantity, 1)
        self.assertEqual(requested_extras[1].unit_price_snapshot, Decimal("15.00"))
        self.assertEqual(requested_extras[1].line_total_snapshot, Decimal("15.00"))

    def test_request_create_allows_offer_with_subservices_without_requested_subservice(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Catalog Optional",
            phone_number="5550000015",
            email="provider.catalog.optional@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="24 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Catalog Optional Service",
            description="Catalog Optional Service",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Catalog Optional Offer",
            billing_unit="fixed",
            price_cents=12500,
            is_active=True,
        )
        ProviderServiceSubservice.objects.create(
            provider_service=provider_service,
            name="Deep Cleaning",
            base_price=Decimal("150.00"),
            is_active=True,
            sort_order=1,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Catalog Optional",
            phone_number="5550000016",
            email="client.catalog.optional@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="25 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "requested_quantity": "1",
                "job_mode": "on_demand",
            },
        )

        self.assertEqual(response.status_code, 302)
        job = Job.objects.get()
        self.assertEqual(job.requested_subservice_name, "")
        self.assertIsNone(job.requested_subservice_id_snapshot)
        self.assertEqual(job.requested_unit_price_snapshot, Decimal("125.00"))
        self.assertEqual(job.requested_subtotal_snapshot, Decimal("125.00"))
        self.assertEqual(job.requested_tax_snapshot, Decimal("18.72"))
        self.assertEqual(job.requested_total_snapshot, Decimal("143.72"))

    def test_request_create_rejects_zero_quantity(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Zero Quantity",
            phone_number="5550000120",
            email="provider.zero.quantity@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="31 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Zero Quantity Service",
            description="Zero Quantity Service",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Standard cleaning",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Zero Quantity",
            phone_number="5550000121",
            email="client.zero.quantity@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="32 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "requested_quantity": "0",
                "job_mode": "on_demand",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quantity must be greater than zero.")
        self.assertEqual(Job.objects.count(), 0)

    def test_request_create_rejects_blank_quantity(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Blank Quantity",
            phone_number="5550000122",
            email="provider.blank.quantity@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="33 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Blank Quantity Service",
            description="Blank Quantity Service",
        )
        provider_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Standard cleaning",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Blank Quantity",
            phone_number="5550000123",
            email="client.blank.quantity@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="34 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(provider_service.pk),
                "requested_quantity": "",
                "job_mode": "on_demand",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quantity is required.")
        self.assertEqual(Job.objects.count(), 0)

    def test_request_create_rejects_subservice_from_another_provider_service(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Subservice Validation",
            phone_number="5550000015",
            email="provider.subservice.validation@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="24 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Subservice Validation",
            description="Subservice Validation",
        )
        main_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Main Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        other_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Other Service",
            billing_unit="fixed",
            price_cents=11000,
            is_active=True,
        )
        invalid_subservice = ProviderServiceSubservice.objects.create(
            provider_service=other_service,
            name="Wrong Subservice",
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Subservice Validation",
            phone_number="5550000016",
            email="client.subservice.validation@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="25 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(main_service.pk),
                "requested_subservice_id": str(invalid_subservice.pk),
                "job_mode": "on_demand",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid subservice.")
        self.assertEqual(Job.objects.count(), 0)

    def test_request_create_rejects_extra_from_another_provider_service(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Extra Validation",
            phone_number="5550000017",
            email="provider.extra.validation@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="26 Provider St",
        )
        service_type = ServiceType.objects.create(
            name="Extra Validation",
            description="Extra Validation",
        )
        main_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Main Extra Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        other_service = ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Other Extra Service",
            billing_unit="fixed",
            price_cents=11000,
            is_active=True,
        )
        invalid_extra = ProviderServiceExtra.objects.create(
            provider_service=other_service,
            name="Wrong Extra",
            is_active=True,
            min_qty=1,
            max_qty=3,
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Extra Validation",
            phone_number="5550000018",
            email="client.extra.validation@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="27 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client.pk
        session.save()

        response = self.client.post(
            f"/request/{provider.provider_id}/",
            data={
                "service_type": str(service_type.pk),
                "provider_service_id": str(main_service.pk),
                "selected_extras": [str(invalid_extra.pk)],
                f"extra_qty_{invalid_extra.pk}": "2",
                "job_mode": "on_demand",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid extra selection.")
        self.assertEqual(Job.objects.count(), 0)


class RequestStatusViewTests(TestCase):
    def _make_job(self, *, status):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Demo",
            contact_last_name="Provider Status",
            phone_number=f"5551000{status.count('a')}201",
            email=f"provider.status.{status}@test.local",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="100 Provider St",
        )
        client = Client.objects.create(
            first_name="Luis",
            last_name="Garcia",
            phone_number=f"5551000{status.count('a')}202",
            email=f"client.status.{status}@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="921 100 e avenue",
            is_phone_verified=True,
            profile_completed=True,
        )
        service_type = ServiceType.objects.create(
            name=f"Status {status}",
            description=f"Status {status}",
        )
        return Job.objects.create(
            selected_provider=provider,
            client=client,
            service_type=service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=status,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="921 100 e avenue",
        )

    def test_request_status_allows_client_cancel_while_waiting_provider_response(self):
        job = self._make_job(status=Job.JobStatus.WAITING_PROVIDER_RESPONSE)

        response = self.client.post(
            reverse("ui:request_status", args=[job.job_id]),
            data={"action": "cancel_request"},
            follow=True,
        )

        job.refresh_from_db()

        self.assertEqual(job.job_status, Job.JobStatus.CANCELLED)
        self.assertEqual(job.cancelled_by, Job.CancellationActor.CLIENT)
        self.assertEqual(job.cancel_reason, Job.CancelReason.CLIENT_CANCELLED)
        self.assertContains(response, "Request cancelled successfully.")
        self.assertContains(response, "Cancelled - Client cancelled the request.")
        event = job.events.get(event_type=JobEvent.EventType.JOB_CANCELLED)
        self.assertEqual(event.actor_role, JobEvent.ActorRole.CLIENT)
        self.assertEqual(event.visible_status, "Cancelled")
        self.assertEqual(event.payload_json.get("source"), "request_status_cancel")

    def test_request_status_shows_emergency_timing_and_next_step(self):
        job = self._make_job(status=Job.JobStatus.WAITING_PROVIDER_RESPONSE)

        response = self.client.get(
            reverse("ui:request_status", args=[job.job_id]),
            {"service_timing": "emergency"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Timing")
        self.assertContains(response, "Emergency")
        self.assertContains(response, "Next step")
        self.assertContains(response, "Waiting for provider response")
        self.assertEqual(response.context["service_timing"], "emergency")
        self.assertEqual(response.context["next_step_label"], "Waiting for provider response")

    def test_request_status_falls_back_to_scheduled_timing_from_job_mode(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Scheduled",
            contact_last_name="Provider Status",
            phone_number="555100099201",
            email="provider.status.scheduled@test.local",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="200 Provider St",
        )
        client = Client.objects.create(
            first_name="Luis",
            last_name="Scheduled Garcia",
            phone_number="555100099202",
            email="client.status.scheduled@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="922 100 e avenue",
            is_phone_verified=True,
            profile_completed=True,
        )
        service_type = ServiceType.objects.create(
            name="Status scheduled",
            description="Status scheduled",
        )
        job = Job.objects.create(
            selected_provider=provider,
            client=client,
            service_type=service_type,
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=2),
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="922 100 e avenue",
        )

        response = self.client.get(reverse("ui:request_status", args=[job.job_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Scheduled")
        self.assertContains(response, "Request submitted")
        self.assertEqual(response.context["service_timing"], "scheduled")
        self.assertEqual(response.context["next_step_label"], "Request submitted")

    def test_request_status_shows_assigned_as_accepted(self):
        job = self._make_job(status=Job.JobStatus.ASSIGNED)

        response = self.client.get(reverse("ui:request_status", args=[job.job_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Status")
        self.assertContains(response, "Accepted")
        self.assertEqual(response.context["job_status_label"], "Accepted")

    def test_request_status_shows_job_event_timeline(self):
        job = self._make_job(status=Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        JobEvent.objects.create(
            job=job,
            event_type=JobEvent.EventType.JOB_CREATED,
            visible_status="Waiting for provider response",
            actor_role=JobEvent.ActorRole.CLIENT,
            payload_json={"source": "test"},
        )

        response = self.client.get(reverse("ui:request_status", args=[job.job_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Job timeline")
        self.assertContains(response, "Waiting for provider response")
        self.assertContains(response, "Job Created")
        self.assertContains(response, "Client")
        self.assertEqual(len(response.context["job_events"]), 1)
        self.assertEqual(
            response.context["job_events"][0].event_type,
            JobEvent.EventType.JOB_CREATED,
        )

    def test_request_status_allows_client_cancel_while_assigned(self):
        job = self._make_job(status=Job.JobStatus.ASSIGNED)
        assignment = JobAssignment.objects.create(
            job=job,
            provider=job.selected_provider,
            assignment_status="assigned",
            is_active=True,
        )

        response = self.client.post(
            reverse("ui:request_status", args=[job.job_id]),
            data={"action": "cancel_request"},
            follow=True,
        )

        job.refresh_from_db()
        assignment.refresh_from_db()

        self.assertEqual(job.job_status, Job.JobStatus.CANCELLED)
        self.assertEqual(job.cancelled_by, Job.CancellationActor.CLIENT)
        self.assertEqual(job.cancel_reason, Job.CancelReason.CLIENT_CANCELLED)
        self.assertEqual(assignment.assignment_status, "cancelled")
        self.assertFalse(assignment.is_active)
        self.assertContains(response, "Request cancelled successfully.")

    def test_request_status_disallows_client_cancel_after_in_progress(self):
        job = self._make_job(status=Job.JobStatus.IN_PROGRESS)

        response = self.client.post(
            reverse("ui:request_status", args=[job.job_id]),
            data={"action": "cancel_request"},
            follow=True,
        )

        job.refresh_from_db()

        self.assertEqual(job.job_status, Job.JobStatus.IN_PROGRESS)
        self.assertContains(response, "This request can no longer be cancelled.")

    def test_request_status_allows_client_cancel_while_posted_after_provider_rejection(self):
        job = self._make_job(status=Job.JobStatus.POSTED)
        job.cancelled_by = Job.CancellationActor.PROVIDER
        job.cancel_reason = Job.CancelReason.PROVIDER_REJECTED
        job.save(update_fields=["cancelled_by", "cancel_reason", "updated_at"])

        response = self.client.post(
            reverse("ui:request_status", args=[job.job_id]),
            data={"action": "cancel_request"},
            follow=True,
        )

        job.refresh_from_db()

        self.assertEqual(job.job_status, Job.JobStatus.CANCELLED)
        self.assertEqual(job.cancelled_by, Job.CancellationActor.CLIENT)
        self.assertEqual(job.cancel_reason, Job.CancelReason.CLIENT_CANCELLED)
        self.assertContains(response, "Request cancelled successfully.")

    def test_request_status_shows_requested_subservice_and_extras_snapshot(self):
        job = self._make_job(status=Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        job.provider_service_name_snapshot = "Standard cleaning"
        job.requested_subservice_name = "Deep Cleaning"
        job.requested_subservice_id_snapshot = 10
        job.requested_quantity_snapshot = Decimal("2.00")
        job.requested_unit_price_snapshot = Decimal("75.00")
        job.requested_billing_unit_snapshot = "hour"
        job.requested_base_line_total_snapshot = Decimal("150.00")
        job.requested_subservice_base_price_snapshot = Decimal("150.00")
        job.requested_subtotal_snapshot = Decimal("220.00")
        job.requested_tax_snapshot = Decimal("32.95")
        job.requested_tax_rate_bps_snapshot = 14975
        job.requested_tax_region_code_snapshot = "QC"
        job.requested_total_snapshot = Decimal("252.95")
        job.save(
            update_fields=[
                "provider_service_name_snapshot",
                "requested_subservice_name",
                "requested_subservice_id_snapshot",
                "requested_quantity_snapshot",
                "requested_unit_price_snapshot",
                "requested_billing_unit_snapshot",
                "requested_base_line_total_snapshot",
                "requested_subservice_base_price_snapshot",
                "requested_subtotal_snapshot",
                "requested_tax_snapshot",
                "requested_tax_rate_bps_snapshot",
                "requested_tax_region_code_snapshot",
                "requested_total_snapshot",
                "updated_at",
            ]
        )
        JobRequestedExtra.objects.create(
            job=job,
            extra_name_snapshot="Extra bathroom",
            quantity=2,
            unit_price_snapshot=Decimal("25.00"),
            line_total_snapshot=Decimal("50.00"),
        )
        JobRequestedExtra.objects.create(
            job=job,
            extra_name_snapshot="Inside fridge",
            quantity=1,
            unit_price_snapshot=Decimal("20.00"),
            line_total_snapshot=Decimal("20.00"),
        )

        response = self.client.get(reverse("ui:request_status", args=[job.job_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Service details")
        self.assertContains(response, "Price breakdown")
        self.assertContains(response, "Main offer")
        self.assertContains(response, "Standard cleaning")
        self.assertContains(response, "Per Hour")
        self.assertContains(response, "$75.00")
        self.assertContains(response, "2.00")
        self.assertContains(response, "Base service total")
        self.assertContains(response, "$150.00")
        self.assertContains(response, "Subservice")
        self.assertContains(response, "Deep Cleaning")
        self.assertContains(response, "Extra bathroom x 2")
        self.assertContains(response, "Inside fridge x 1")
        self.assertContains(response, "Subtotal")
        self.assertContains(response, "Taxes (QC)")
        self.assertContains(response, "Total")
        self.assertContains(response, "$252.95")
        self.assertContains(response, "Return to Marketplace")

    def test_request_status_shows_back_to_activity_link_when_next_present(self):
        job = self._make_job(status=Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        next_url = "/clients/activity/?status=completed&range=30d&page=2"

        response = self.client.get(
            reverse("ui:request_status", args=[job.job_id]),
            {"next": next_url},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<a class="btn btn-secondary back-to-activity" href="{next_url}">Back to Activity</a>',
            html=True,
        )

    def test_request_status_preserves_next_after_post_redirect(self):
        job = self._make_job(status=Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        next_url = "/clients/activity/?status=completed&range=30d&page=2"

        response = self.client.post(
            reverse("ui:request_status", args=[job.job_id]),
            data={"action": "cancel_request", "next": next_url},
            follow=True,
        )

        self.assertEqual(response.redirect_chain[-1][0], "{}?{}".format(
            reverse("ui:request_status", args=[job.job_id]),
            urlencode({"next": next_url}),
        ))
        self.assertContains(response, "Back to Activity")


class ProviderFinancialConsistencyTests(TestCase):
    def _login_provider(self, provider):
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

    def test_job_snapshot_matches_provider_ticket_and_financial_summary(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Financial Consistency Provider",
            contact_first_name="Financial",
            contact_last_name="Consistency",
            phone_number="+15145551221",
            email="financial.consistency.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="123 Test St",
        )
        client = Client.objects.create(
            first_name="Financial",
            last_name="Client",
            email="financial.consistency.client@test.local",
            phone_number="+15145551222",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="124 Test St",
        )
        service_type = ServiceType.objects.create(
            name="Financial Consistency Service",
            description="Financial Consistency Service",
        )
        job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Financial Consistency Offer",
            requested_subtotal_snapshot=Decimal("100.00"),
            requested_tax_snapshot=Decimal("14.98"),
            requested_total_snapshot=Decimal("114.98"),
            requested_tax_region_code_snapshot="QC",
            requested_tax_rate_bps_snapshot=14975,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="123 Test St",
        )
        ticket = ProviderTicket.objects.create(
            provider=provider,
            ref_type="job",
            ref_id=job.job_id,
            ticket_no="PT-CONSISTENCY-0001",
            stage=ProviderTicket.Stage.ESTIMATE,
            status=ProviderTicket.Status.OPEN,
            subtotal_cents=10000,
            tax_cents=1498,
            total_cents=11498,
            currency="CAD",
            tax_region_code="QC",
        )
        ProviderTicketLine.objects.create(
            ticket=ticket,
            line_no=1,
            line_type=ProviderTicketLine.LineType.BASE,
            description="Service",
            qty=1,
            unit_price_cents=10000,
            line_subtotal_cents=10000,
            tax_rate_bps=14975,
            tax_cents=1498,
            line_total_cents=11498,
            tax_region_code="QC",
            tax_code="",
            meta={},
        )
        PlatformLedgerEntry.objects.create(
            job=job,
            gross_cents=11498,
            tax_cents=1498,
            fee_cents=0,
            net_provider_cents=11498,
            platform_revenue_cents=0,
            tax_region_code="QC",
            is_final=True,
        )

        self.assertEqual(job.requested_subtotal_snapshot, Decimal("100.00"))
        self.assertEqual(job.requested_tax_snapshot, Decimal("14.98"))
        self.assertEqual(job.requested_total_snapshot, Decimal("114.98"))

        self.assertEqual(ticket.subtotal_cents, 10000)
        self.assertEqual(ticket.tax_cents, 1498)
        self.assertEqual(ticket.total_cents, 11498)
        self.assertEqual(ticket.tax_region_code, "QC")

        self.assertEqual(
            Decimal(ticket.subtotal_cents) / Decimal("100"),
            job.requested_subtotal_snapshot,
        )
        self.assertEqual(
            Decimal(ticket.tax_cents) / Decimal("100"),
            job.requested_tax_snapshot,
        )
        self.assertEqual(
            Decimal(ticket.total_cents) / Decimal("100"),
            job.requested_total_snapshot,
        )

        self._login_provider(provider)
        response = self.client.get(reverse("provider_financial_summary"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["activity_analytics"]["total_gross"], Decimal("114.98"))
        self.assertEqual(
            response.context["activity_analytics"]["total_provider_earnings"],
            Decimal("114.98"),
        )
        self.assertEqual(
            response.context["activity_analytics"]["total_platform_fees"],
            Decimal("0.00"),
        )
        self.assertEqual(len(response.context["monthly_revenue"]), 1)
        self.assertEqual(response.context["monthly_revenue"][0]["gross"], Decimal("114.98"))
        self.assertContains(response, "Financial Summary")
        self.assertContains(response, "114.98")

    def test_job_ticket_and_activity_views_stay_financially_aligned(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Activity Consistency Provider",
            contact_first_name="Activity",
            contact_last_name="Consistency",
            phone_number="+15145551223",
            email="activity.consistency.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="125 Test St",
        )
        client = Client.objects.create(
            first_name="Activity",
            last_name="Client",
            email="activity.consistency.client@test.local",
            phone_number="+15145551224",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="126 Test St",
        )
        service_type = ServiceType.objects.create(
            name="Activity Consistency Service",
            description="Activity Consistency Service",
        )
        job = Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Activity Consistency Offer",
            requested_subtotal_snapshot=Decimal("100.00"),
            requested_tax_snapshot=Decimal("14.98"),
            requested_total_snapshot=Decimal("114.98"),
            requested_tax_region_code_snapshot="QC",
            requested_tax_rate_bps_snapshot=14975,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="125 Test St",
        )
        ticket = ProviderTicket.objects.create(
            provider=provider,
            ref_type="job",
            ref_id=job.job_id,
            ticket_no="PT-CONSISTENCY-0002",
            stage=ProviderTicket.Stage.ESTIMATE,
            status=ProviderTicket.Status.OPEN,
            subtotal_cents=10000,
            tax_cents=1498,
            total_cents=11498,
            currency="CAD",
            tax_region_code="QC",
        )
        ProviderTicketLine.objects.create(
            ticket=ticket,
            line_no=1,
            line_type=ProviderTicketLine.LineType.BASE,
            description="Service",
            qty=1,
            unit_price_cents=10000,
            line_subtotal_cents=10000,
            tax_rate_bps=14975,
            tax_cents=1498,
            line_total_cents=11498,
            tax_region_code="QC",
            tax_code="",
            meta={},
        )
        PlatformLedgerEntry.objects.create(
            job=job,
            gross_cents=11498,
            tax_cents=1498,
            fee_cents=2000,
            net_provider_cents=9498,
            platform_revenue_cents=2000,
            tax_region_code="QC",
            is_final=True,
        )

        self._login_provider(provider)

        activity_response = self.client.get(reverse("provider_activity"))

        self.assertEqual(activity_response.status_code, 200)
        self.assertContains(activity_response, "114.98")
        self.assertContains(activity_response, "94.98")
        self.assertContains(activity_response, "20.00")
        self.assertEqual(
            activity_response.context["activity_analytics"]["total_gross"],
            Decimal("114.98"),
        )
        self.assertEqual(
            activity_response.context["activity_analytics"]["total_provider_earnings"],
            Decimal("94.98"),
        )
        self.assertEqual(
            activity_response.context["activity_analytics"]["total_platform_fees"],
            Decimal("20.00"),
        )
        self.assertEqual(len(activity_response.context["activity_rows"]), 1)
        row = activity_response.context["activity_rows"][0]
        self.assertEqual(row.gross_cents, 11498)
        self.assertEqual(row.provider_net_cents, 9498)
        self.assertEqual(row.platform_fee_cents, 2000)

        summary_response = self.client.get(reverse("provider_financial_summary"))

        self.assertEqual(summary_response.status_code, 200)
        self.assertEqual(
            summary_response.context["activity_analytics"]["total_gross"],
            Decimal("114.98"),
        )
        self.assertEqual(
            summary_response.context["activity_analytics"]["total_provider_earnings"],
            Decimal("94.98"),
        )
        self.assertEqual(
            summary_response.context["activity_analytics"]["total_platform_fees"],
            Decimal("20.00"),
        )
        self.assertEqual(len(summary_response.context["monthly_revenue"]), 1)
        self.assertEqual(summary_response.context["monthly_revenue"][0]["gross"], Decimal("114.98"))


class ProviderJobsViewTests(TestCase):
    def test_provider_jobs_redirects_to_register_without_provider_session(self):
        response = self.client.get(reverse("ui:provider_jobs"))

        self.assertRedirects(response, reverse("provider_register"))

    def test_provider_jobs_shows_client_service_schedule_and_address(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Demo",
            contact_last_name="Provider1",
            phone_number="5550000200",
            email="provider.jobs@test.local",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="100 Provider St",
        )
        client = Client.objects.create(
            first_name="Luis",
            last_name="Garcia",
            phone_number="5550000201",
            email="client.jobs@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="921 100 e avenue",
            is_phone_verified=True,
            profile_completed=True,
        )
        service_type = ServiceType.objects.create(
            name="Cleaning Service",
            description="Cleaning Service",
        )
        Job.objects.create(
            selected_provider=provider,
            client=client,
            service_type=service_type,
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            scheduled_date=timezone.localdate() + timedelta(days=1),
            scheduled_start_time="15:29",
            is_asap=False,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="921 100 e avenue",
        )

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("ui:provider_jobs"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provider Jobs")
        self.assertContains(response, "Luis Garcia")
        self.assertContains(response, "Cleaning Service")
        self.assertContains(response, "Requested time")
        self.assertContains(response, "Service mode")
        self.assertContains(response, "Scheduled")
        self.assertContains(response, "Postal Code")
        self.assertContains(response, "H7W4A2")
        self.assertContains(response, "Accept")
        self.assertContains(response, "Decline")

    def test_provider_jobs_shows_requested_subservice_and_extras_snapshot(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Demo",
            contact_last_name="Provider Snapshot",
            phone_number="5550000208",
            email="provider.snapshot@test.local",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="203 Provider St",
        )
        client = Client.objects.create(
            first_name="Luis",
            last_name="Garcia",
            phone_number="5550000209",
            email="client.snapshot@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="924 100 e avenue",
            is_phone_verified=True,
            profile_completed=True,
        )
        service_type = ServiceType.objects.create(
            name="Snapshot Service",
            description="Snapshot Service",
        )
        job = Job.objects.create(
            selected_provider=provider,
            client=client,
            service_type=service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="924 100 e avenue",
            provider_service_name_snapshot="Standard cleaning",
            requested_subservice_name="Deep Cleaning",
            requested_subservice_id_snapshot=99,
            requested_quantity_snapshot=Decimal("3.00"),
            requested_unit_price_snapshot=Decimal("46.67"),
            requested_billing_unit_snapshot="sqm",
            requested_base_line_total_snapshot=Decimal("140.00"),
            requested_subservice_base_price_snapshot=Decimal("140.00"),
            requested_subtotal_snapshot=Decimal("180.00"),
            requested_total_snapshot=Decimal("180.00"),
        )
        JobRequestedExtra.objects.create(
            job=job,
            extra_name_snapshot="Extra bathroom",
            quantity=2,
            unit_price_snapshot=Decimal("15.00"),
            line_total_snapshot=Decimal("30.00"),
        )
        JobRequestedExtra.objects.create(
            job=job,
            extra_name_snapshot="Inside fridge",
            quantity=1,
            unit_price_snapshot=Decimal("10.00"),
            line_total_snapshot=Decimal("10.00"),
        )

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("ui:provider_jobs"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pricing")
        self.assertContains(response, "Main Offer")
        self.assertContains(response, "Standard cleaning")
        self.assertContains(response, "Per Square Meter")
        self.assertContains(response, "$46.67")
        self.assertContains(response, "3.00")
        self.assertContains(response, "Base Line Total")
        self.assertContains(response, "$140.00")
        self.assertContains(response, "Requested details")
        self.assertContains(response, "Subservice: Deep Cleaning")
        self.assertContains(response, "Extra bathroom x 2")
        self.assertContains(response, "Inside fridge x 1")
        self.assertContains(response, "Estimated Total")
        self.assertContains(response, "$180.00")

    def test_provider_job_action_redirects_to_register_without_provider_session(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Demo",
            contact_last_name="Provider2",
            phone_number="5550000202",
            email="provider.action@test.local",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="200 Provider St",
        )
        client = Client.objects.create(
            first_name="Luis",
            last_name="Garcia",
            phone_number="5550000203",
            email="client.action@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="921 100 e avenue",
            is_phone_verified=True,
            profile_completed=True,
        )
        service_type = ServiceType.objects.create(
            name="Provider Action Test",
            description="Provider Action Test",
        )
        job = Job.objects.create(
            selected_provider=provider,
            client=client,
            service_type=service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="921 100 e avenue",
        )

        response = self.client.post(
            reverse("ui:provider_job_action", args=[job.job_id]),
            data={"action": "accept"},
        )

        self.assertRedirects(response, reverse("provider_register"))

    def test_accept_job_invalid_state_returns_400(self):
        service_type = ServiceType.objects.create(
            name="Provider Action Invalid State Test",
            description="Provider Action Invalid State Test",
        )
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Demo",
            contact_last_name="Provider3",
            phone_number="5550000204",
            email="provider.action.invalid@test.local",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="201 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Provider Action Service",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        client = Client.objects.create(
            first_name="Luis",
            last_name="Garcia",
            phone_number="5550000205",
            email="client.action.invalid@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="922 100 e avenue",
            is_phone_verified=True,
            profile_completed=True,
        )
        job = Job.objects.create(
            selected_provider=provider,
            client=client,
            service_type=service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.ASSIGNED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="922 100 e avenue",
        )

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.post(
            reverse("ui:provider_job_action", args=[job.job_id]),
            data={"action": "accept"},
        )

        self.assertEqual(response.status_code, 400)

    def test_provider_reject_recycles_request_and_closes_assignment(self):
        service_type = ServiceType.objects.create(
            name="Provider Reject Recycle Test",
            description="Provider Reject Recycle Test",
        )
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Demo",
            contact_last_name="Provider Reject",
            phone_number="5550000206",
            email="provider.reject.recycle@test.local",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="202 Provider St",
        )
        client = Client.objects.create(
            first_name="Luis",
            last_name="Garcia",
            phone_number="5550000207",
            email="client.reject.recycle@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="923 100 e avenue",
            is_phone_verified=True,
            profile_completed=True,
        )
        job = Job.objects.create(
            selected_provider=provider,
            client=client,
            service_type=service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7W4A2",
            address_line1="923 100 e avenue",
        )
        assignment = JobAssignment.objects.create(
            job=job,
            provider=provider,
            assignment_status="assigned",
            is_active=True,
        )

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.post(
            reverse("ui:provider_job_action", args=[job.job_id]),
            data={"action": "reject"},
            follow=True,
        )

        job.refresh_from_db()
        assignment.refresh_from_db()

        self.assertEqual(job.job_status, Job.JobStatus.POSTED)
        self.assertIsNone(job.selected_provider_id)
        self.assertEqual(job.cancelled_by, Job.CancellationActor.PROVIDER)
        self.assertEqual(job.cancel_reason, Job.CancelReason.PROVIDER_REJECTED)
        self.assertEqual(assignment.assignment_status, "cancelled")
        self.assertFalse(assignment.is_active)
        self.assertContains(response, "Request declined.")


class MarketplaceSearchViewTests(TestCase):
    def _login_client(self, client_obj):
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

    def _scheduled_marketplace_query(self, **kwargs):
        params = {"service_timing": "scheduled"}
        params.update(kwargs)
        return params

    def _create_provider_with_offer(
        self,
        *,
        email,
        phone_number,
        city,
        service_type,
        price_cents,
        provider_type="self_employed",
        postal_prefix=None,
    ):
        display_first_name = email.split(".", 1)[0].split("@", 1)[0].title()
        provider = Provider.objects.create(
            provider_type=provider_type,
            legal_name="Provider Legal" if provider_type == "self_employed" else "",
            company_name="Provider Company" if provider_type == "company" else None,
            business_registration_number="REG-001" if provider_type == "company" else "",
            contact_first_name=display_first_name,
            contact_last_name="Provider",
            phone_number=phone_number,
            email=email,
            province="QC",
            city=city,
            postal_code="H1A1A1",
            address_line1="100 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area=city,
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city=city,
            province="QC",
            postal_prefix=postal_prefix,
            is_active=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name=f"{service_type.name} Service",
            billing_unit="fixed",
            price_cents=price_cents,
            is_active=True,
        )
        return provider

    def _create_verified_client(self):
        client_obj = Client.objects.create(
            first_name="Marketplace",
            last_name="Verified",
            phone_number="5550000411",
            email="marketplace.verified@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="1 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)
        return client_obj

    def test_marketplace_search_shows_client_navigation_links(self):
        client_obj = Client.objects.create(
            first_name="Marketplace",
            last_name="Navigation",
            phone_number="5550000412",
            email="marketplace.navigation@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="2 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        response = self.client.get(reverse("ui:marketplace_search"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("client_dashboard"))
        self.assertContains(response, reverse("ui:marketplace_search"))
        self.assertContains(response, reverse("client_activity"))
        self.assertContains(response, reverse("client_profile"))
        self.assertContains(response, reverse("client_billing"))
        self.assertContains(
            response,
            f'<a class="nodo-subnav__item active" href="{reverse("ui:marketplace_search")}" aria-current="page">Marketplace</a>',
            html=True,
        )
        self.assertContains(response, "Marketplace Navigation \u2013 Client")
        self.assertNotContains(response, ">Account<", html=False)

    def test_marketplace_search_defaults_to_profile_address_toggle(self):
        client_obj = self._create_verified_client()

        response = self.client.get(reverse("ui:marketplace_search"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form_data"]["use_profile_address"])
        self.assertEqual(response.context["form_data"]["province"], client_obj.province)
        self.assertEqual(response.context["form_data"]["city"], client_obj.city)
        self.assertEqual(response.context["form_data"]["postal_code"], client_obj.postal_code)
        response_html = response.content.decode()
        self.assertRegex(
            response_html,
            r'<input[^>]+name="use_profile_address"[^>]+id="use_profile_address"[^>]+value="1"[^>]+checked',
        )
        self.assertRegex(
            response_html,
            r'<input[^>]+id="city"[^>]+value="Laval"[^>]+readonly',
        )
        self.assertRegex(
            response_html,
            r'<input[^>]+id="postal_code"[^>]+value="H7A1A1"[^>]+readonly',
        )

    def test_marketplace_search_allows_manual_location_when_profile_toggle_disabled(self):
        self._create_verified_client()

        response = self.client.get(
            reverse("ui:marketplace_search"),
            {
                "use_profile_address": "0",
                "province": "QC",
                "city": "Montreal",
                "postal_code": "H2X1A4",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form_data"]["use_profile_address"])
        self.assertEqual(response.context["form_data"]["city"], "Montreal")
        self.assertEqual(response.context["form_data"]["postal_code"], "H2X1A4")
        response_html = response.content.decode()
        self.assertRegex(
            response_html,
            r'<input[^>]+id="city"[^>]+value="Montreal"',
        )
        self.assertRegex(
            response_html,
            r'<input[^>]+id="postal_code"[^>]+value="H2X1A4"',
        )
        self.assertNotRegex(
            response_html,
            r'<input[^>]+id="city"[^>]+readonly',
        )
        self.assertNotRegex(
            response_html,
            r'<input[^>]+id="postal_code"[^>]+readonly',
        )

    def test_marketplace_results_shows_client_navigation_links(self):
        client_obj = Client.objects.create(
            first_name="Marketplace Results",
            last_name="Navigation",
            phone_number="5550000419",
            email="marketplace.results.navigation@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="5 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        service_type = ServiceType.objects.create(name="Results HVAC", description="Results HVAC")
        self._create_provider_with_offer(
            email="results.provider@test.local",
            phone_number="5550000420",
            city="Laval",
            service_type=service_type,
            price_cents=12000,
        )

        response = self.client.post(
            reverse("ui:marketplace_results"),
            {
                "service_type": service_type.service_type_id,
                "province": "QC",
                "city": "Laval",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("client_dashboard"))
        self.assertContains(response, reverse("ui:marketplace_search"))
        self.assertContains(response, reverse("client_activity"))
        self.assertContains(response, reverse("client_profile"))
        self.assertContains(response, reverse("client_billing"))
        self.assertContains(
            response,
            f'<a class="nodo-subnav__item active" href="{reverse("ui:marketplace_search")}" aria-current="page">Marketplace</a>',
            html=True,
        )

    def test_marketplace_search_prefers_city_results_for_selected_service_type(self):
        client_obj = Client.objects.create(
            first_name="Search",
            last_name="Client",
            phone_number="5550000400",
            email="marketplace.city@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="1 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        hvac = ServiceType.objects.create(name="HVAC", description="HVAC")
        cleaning = ServiceType.objects.create(name="Cleaning", description="Cleaning")

        city_provider = self._create_provider_with_offer(
            email="city.provider@test.local",
            phone_number="5550000401",
            city="Laval",
            service_type=hvac,
            price_cents=12000,
        )
        province_provider = self._create_provider_with_offer(
            email="province.provider@test.local",
            phone_number="5550000402",
            city="Montreal",
            service_type=hvac,
            price_cents=9000,
        )
        self._create_provider_with_offer(
            email="city.cleaning.provider@test.local",
            phone_number="5550000403",
            city="Laval",
            service_type=cleaning,
            price_cents=11000,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(service_type=hvac.service_type_id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, city_provider.contact_first_name)
        self.assertNotContains(response, province_provider.contact_first_name)
        self.assertContains(response, "HVAC")

    def test_marketplace_search_uses_city_results_when_postal_code_is_provided(self):
        client_obj = Client.objects.create(
            first_name="Postal",
            last_name="Client",
            phone_number="5550000413",
            email="marketplace.postal@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="3 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        hvac = ServiceType.objects.create(name="Postal HVAC", description="Postal HVAC")
        city_provider = self._create_provider_with_offer(
            email="city.postal.provider@test.local",
            phone_number="5550000414",
            city="Laval",
            service_type=hvac,
            price_cents=12000,
            postal_prefix="H2X",
        )
        prefix_match_other_city_provider = self._create_provider_with_offer(
            email="prefix.match.provider@test.local",
            phone_number="5550000415",
            city="Montreal",
            service_type=hvac,
            price_cents=9000,
            postal_prefix="H7A",
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(
                service_type=hvac.service_type_id,
                postal_code="h7a 9z9",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"{city_provider.contact_first_name} {city_provider.contact_last_name}",
        )
        self.assertNotContains(
            response,
            f"{prefix_match_other_city_provider.contact_first_name} {prefix_match_other_city_provider.contact_last_name}",
        )
        self.assertContains(response, "Postal HVAC")

    def test_marketplace_search_uses_city_results_for_urgent_when_postal_code_is_provided(self):
        client_obj = Client.objects.create(
            first_name="Urgent",
            last_name="Postal",
            phone_number="5550000419",
            email="marketplace.urgent.postal@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="3 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        hvac = ServiceType.objects.create(name="Urgent Postal HVAC", description="Urgent Postal HVAC")
        city_provider = self._create_provider_with_offer(
            email="urgentcity.provider@test.local",
            phone_number="5550000420",
            city="Laval",
            service_type=hvac,
            price_cents=12000,
            postal_prefix="H2X",
        )
        prefix_match_other_city_provider = self._create_provider_with_offer(
            email="wrongcity.provider@test.local",
            phone_number="5550000421",
            city="Montreal",
            service_type=hvac,
            price_cents=9000,
            postal_prefix="H7A",
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            {
                "service_type": hvac.service_type_id,
                "service_timing": "urgent",
                "postal_code": "h7a 9z9",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"{city_provider.contact_first_name} {city_provider.contact_last_name}",
        )
        self.assertNotContains(
            response,
            f"{prefix_match_other_city_provider.contact_first_name} {prefix_match_other_city_provider.contact_last_name}",
        )
        self.assertContains(response, "Urgent Postal HVAC")

    def test_marketplace_search_filters_by_only_insured_flag(self):
        client_obj = Client.objects.create(
            first_name="Insured",
            last_name="Client",
            phone_number="5550000414",
            email="marketplace.insured@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="4 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        hvac = ServiceType.objects.create(name="Insured HVAC", description="Insured HVAC")
        insured_provider = self._create_provider_with_offer(
            email="insured.provider@test.local",
            phone_number="5550000415",
            city="Laval",
            service_type=hvac,
            price_cents=12000,
        )
        uninsured_provider = self._create_provider_with_offer(
            email="uninsured.provider@test.local",
            phone_number="5550000416",
            city="Laval",
            service_type=hvac,
            price_cents=9000,
        )
        ProviderInsurance.objects.create(
            provider=insured_provider,
            has_insurance=True,
            insurance_company="Verified Insurance Co",
            policy_number="POL-001",
            is_verified=True,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(
                service_type=hvac.service_type_id,
                only_insured="1",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, insured_provider.contact_first_name)
        self.assertNotContains(response, uninsured_provider.contact_first_name)
        self.assertTrue(response.context["only_insured"])

    def test_marketplace_search_supports_only_insured_and_only_certified_together(self):
        client_obj = Client.objects.create(
            first_name="Dual Filter",
            last_name="Client",
            phone_number="5550000417",
            email="marketplace.dual.filter@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="4 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        hvac = ServiceType.objects.create(name="Dual Filter HVAC", description="Dual Filter HVAC")
        insured_provider = self._create_provider_with_offer(
            email="dualfilter.provider@test.local",
            phone_number="5550000418",
            city="Laval",
            service_type=hvac,
            price_cents=12000,
        )
        ProviderInsurance.objects.create(
            provider=insured_provider,
            has_insurance=True,
            insurance_company="Verified Insurance Co",
            policy_number="POL-002",
            is_verified=True,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(
                service_type=hvac.service_type_id,
                only_insured="1",
                only_certified="1",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"{insured_provider.contact_first_name} {insured_provider.contact_last_name}",
        )
        self.assertTrue(response.context["only_insured"])
        self.assertTrue(response.context["only_certified"])

    def test_marketplace_search_dedupes_multiple_provider_services_into_one_card(self):
        client_obj = Client.objects.create(
            first_name="Deduped",
            last_name="Client",
            phone_number="5550000419",
            email="marketplace.dedupe@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="4 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        cleaning = ServiceType.objects.create(name="Deduped Cleaning", description="Deduped Cleaning")
        provider = self._create_provider_with_offer(
            email="dedupe.provider@test.local",
            phone_number="5550000420",
            city="Laval",
            service_type=cleaning,
            price_cents=12000,
        )
        primary_offer = ProviderService.objects.get(
            provider=provider,
            service_type=cleaning,
            custom_name=f"{cleaning.name} Service",
        )
        ProviderServiceSubservice.objects.create(
            provider_service=primary_offer,
            name="Deep Clean",
            base_price=Decimal("150.00"),
            is_active=True,
            sort_order=1,
        )
        ProviderServiceExtra.objects.create(
            provider_service=primary_offer,
            name="Window Cleaning",
            unit_price=Decimal("25.00"),
            is_active=True,
            min_qty=1,
            max_qty=5,
            sort_order=1,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=cleaning,
            custom_name="ADDON: Deep cleaning",
            billing_unit="fixed",
            price_cents=15000,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(service_type=cleaning.service_type_id),
        )

        self.assertEqual(response.status_code, 200)
        provider_cards = response.context["providers"]
        self.assertEqual(len(provider_cards), 1)
        self.assertEqual(provider_cards[0].provider_id, provider.provider_id)
        self.assertEqual(provider_cards[0].card_primary_service, "Deduped Cleaning")
        self.assertEqual(provider_cards[0].card_primary_subservice, "")
        self.assertEqual(provider_cards[0].card_extra_preview, "")
        self.assertContains(
            response,
            f"{provider.contact_first_name} {provider.contact_last_name}",
            count=1,
        )
        self.assertNotContains(response, "Window Cleaning")

    def test_marketplace_search_keeps_city_results_for_uncovered_postal_prefix(self):
        client_obj = Client.objects.create(
            first_name="Postal None",
            last_name="Client",
            phone_number="5550000416",
            email="marketplace.postal.none@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="4 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        hvac = ServiceType.objects.create(name="Postal None HVAC", description="Postal None HVAC")
        city_provider = self._create_provider_with_offer(
            email="covered.provider@test.local",
            phone_number="5550000417",
            city="Laval",
            service_type=hvac,
            price_cents=12000,
            postal_prefix="H7A",
        )
        uncovered_provider = self._create_provider_with_offer(
            email="outside.provider@test.local",
            phone_number="5550000418",
            city="Montreal",
            service_type=hvac,
            price_cents=9000,
            postal_prefix="H2X",
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(
                service_type=hvac.service_type_id,
                postal_code="K1A 0B1",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"{city_provider.contact_first_name} {city_provider.contact_last_name}",
        )
        self.assertNotContains(
            response,
            f"{uncovered_provider.contact_first_name} {uncovered_provider.contact_last_name}",
        )
        self.assertNotContains(response, "No providers found.")
        self.assertContains(response, "Postal None HVAC")

    def test_marketplace_search_requires_city_match_for_selected_service_type(self):
        client_obj = Client.objects.create(
            first_name="Province",
            last_name="Client",
            phone_number="5550000410",
            email="marketplace.province@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="2 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        hvac = ServiceType.objects.create(name="HVAC", description="HVAC")
        cleaning = ServiceType.objects.create(name="Cleaning", description="Cleaning")
        self._create_provider_with_offer(
            email="city.cleaning.provider@test.local",
            phone_number="5550000412",
            city="Laval",
            service_type=cleaning,
            price_cents=8500,
        )
        province_provider = self._create_provider_with_offer(
            email="remote.province.provider@test.local",
            phone_number="5550000411",
            city="Montreal",
            service_type=hvac,
            price_cents=9500,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(service_type=hvac.service_type_id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, province_provider.contact_first_name)
        self.assertContains(response, "No providers found.")
        self.assertContains(response, "HVAC")

    def test_marketplace_search_filters_by_provider_name_subservice_and_extra(self):
        client_obj = Client.objects.create(
            first_name="Manual",
            last_name="Search",
            phone_number="5550000419",
            email="marketplace.search.manual@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="5 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        cleaning = ServiceType.objects.create(name="Cleaning", description="Cleaning")

        manual_provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Provider Manual Test",
            legal_name="Provider Manual Test",
            business_registration_number="REG-MANUAL-001",
            contact_first_name="Provider",
            contact_last_name="Manual",
            phone_number="5550000420",
            email="provider.manual.marketplace@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="6 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area="Laval",
        )
        ProviderServiceArea.objects.create(
            provider=manual_provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        manual_offer = ProviderService.objects.create(
            provider=manual_provider,
            service_type=cleaning,
            custom_name="Cleaning",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )
        ProviderServiceSubservice.objects.create(
            provider_service=manual_offer,
            name="Deep Cleaning",
            base_price=Decimal("150.00"),
            is_active=True,
            sort_order=1,
        )
        ProviderServiceExtra.objects.create(
            provider_service=manual_offer,
            name="Inside Fridge",
            unit_price=Decimal("25.00"),
            is_active=True,
            min_qty=1,
            max_qty=5,
            sort_order=1,
        )

        other_provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Other Provider Team",
            legal_name="Other Provider Team",
            business_registration_number="REG-OTHER-001",
            contact_first_name="Other",
            contact_last_name="Provider",
            phone_number="5550000421",
            email="other.provider.marketplace@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="7 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area="Laval",
        )
        ProviderServiceArea.objects.create(
            provider=other_provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=other_provider,
            service_type=cleaning,
            custom_name="Basic Cleaning",
            billing_unit="fixed",
            price_cents=9000,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(
                service_type=cleaning.service_type_id,
                postal_code="H7A1A1",
                q="provider manual",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provider Manual Test")
        self.assertNotContains(response, "Other Provider Team")
        self.assertContains(response, 'name="q"')
        self.assertContains(response, 'value="provider manual"', html=False)
        self.assertContains(response, "Search provider, service, skill or extra")
        self.assertContains(response, 'provider-card provider-card--compact', html=False)

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(
                service_type=cleaning.service_type_id,
                postal_code="H7A1A1",
                q="manual",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provider Manual Test")
        self.assertNotContains(response, "Other Provider Team")

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(
                service_type=cleaning.service_type_id,
                postal_code="H7A1A1",
                q="deep cleaning",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provider Manual Test")
        self.assertNotContains(response, "Other Provider Team")

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(
                service_type=cleaning.service_type_id,
                postal_code="H7A1A1",
                q="inside fridge",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provider Manual Test")
        self.assertNotContains(response, "Other Provider Team")

    def test_marketplace_filters_by_provider_name(self):
        client_obj = Client.objects.create(
            first_name="Provider",
            last_name="Name Filter",
            phone_number="5550000422",
            email="marketplace.provider.name@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="8 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        cleaning = ServiceType.objects.create(
            name="Provider Name Cleaning",
            description="Provider Name Cleaning",
        )

        matching_provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Provider Manual Test",
            legal_name="Provider Manual Test",
            business_registration_number="REG-PROVIDER-001",
            contact_first_name="Provider",
            contact_last_name="Manual",
            phone_number="5550000423",
            email="provider.name.manual@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="9 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area="Laval",
        )
        ProviderServiceArea.objects.create(
            provider=matching_provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=matching_provider,
            service_type=cleaning,
            custom_name="Manual Provider Cleaning",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )

        other_provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Other Provider Team",
            legal_name="Other Provider Team",
            business_registration_number="REG-PROVIDER-002",
            contact_first_name="Other",
            contact_last_name="Provider",
            phone_number="5550000424",
            email="provider.name.other@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="10 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area="Laval",
        )
        ProviderServiceArea.objects.create(
            provider=other_provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=other_provider,
            service_type=cleaning,
            custom_name="Other Provider Cleaning",
            billing_unit="fixed",
            price_cents=9000,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(
                service_type=cleaning.service_type_id,
                postal_code="H7A1A1",
                provider_name="manual",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provider Manual Test")
        self.assertNotContains(response, "Other Provider Team")
        self.assertContains(response, 'name="provider_name"', html=False)
        self.assertContains(response, 'value="manual"', html=False)

    def test_marketplace_provider_name_filter_preserves_limit_and_other_filters(self):
        client_obj = Client.objects.create(
            first_name="Provider",
            last_name="Combined Filter",
            phone_number="5550000425",
            email="marketplace.provider.combined@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="11 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        cleaning = ServiceType.objects.create(
            name="Provider Combined Cleaning",
            description="Provider Combined Cleaning",
        )
        hvac = ServiceType.objects.create(
            name="Provider Combined HVAC",
            description="Provider Combined HVAC",
        )

        matching_names = []
        for index in range(22):
            provider = Provider.objects.create(
                provider_type=Provider.TYPE_COMPANY,
                company_name=f"Manual Match {index + 1:02d}",
                legal_name=f"Manual Match {index + 1:02d}",
                business_registration_number=f"REG-MATCH-{index + 1:03d}",
                contact_first_name="Manual",
                contact_last_name=f"Match{index + 1:02d}",
                phone_number=f"5551000{index + 1:03d}",
                email=f"provider.manual.match.{index + 1:02d}@test.local",
                province="QC",
                city="Laval",
                postal_code="H7A1A1",
                address_line1=f"{100 + index} Match St",
                is_phone_verified=True,
                profile_completed=True,
                billing_profile_completed=True,
                accepts_terms=True,
                service_area="Laval",
            )
            ProviderServiceArea.objects.create(
                provider=provider,
                city="Laval",
                province="QC",
                postal_prefix="H7A",
                is_active=True,
            )
            offer = ProviderService.objects.create(
                provider=provider,
                service_type=cleaning,
                custom_name=f"Manual Match Service {index + 1:02d}",
                billing_unit="fixed",
                price_cents=10000 + index,
                is_active=True,
            )
            ProviderServiceSubservice.objects.create(
                provider_service=offer,
                name="Deep Cleaning",
                base_price=Decimal("150.00"),
                is_active=True,
                sort_order=1,
            )
            matching_names.append(provider.company_name)

        no_subservice_provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Manual No Deep",
            legal_name="Manual No Deep",
            business_registration_number="REG-NODEEP-001",
            contact_first_name="Manual",
            contact_last_name="NoDeep",
            phone_number="5550000426",
            email="provider.manual.no.deep@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="12 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area="Laval",
        )
        ProviderServiceArea.objects.create(
            provider=no_subservice_provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=no_subservice_provider,
            service_type=cleaning,
            custom_name="Manual No Deep Service",
            billing_unit="fixed",
            price_cents=13000,
            is_active=True,
        )

        other_service_provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Manual Other Service",
            legal_name="Manual Other Service",
            business_registration_number="REG-OTHERSVC-001",
            contact_first_name="Manual",
            contact_last_name="OtherService",
            phone_number="5550000427",
            email="provider.manual.other.service@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="13 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area="Laval",
        )
        ProviderServiceArea.objects.create(
            provider=other_service_provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        other_offer = ProviderService.objects.create(
            provider=other_service_provider,
            service_type=hvac,
            custom_name="Manual Other HVAC",
            billing_unit="fixed",
            price_cents=13500,
            is_active=True,
        )
        ProviderServiceSubservice.objects.create(
            provider_service=other_offer,
            name="Deep Cleaning",
            base_price=Decimal("160.00"),
            is_active=True,
            sort_order=1,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(
                service_type=cleaning.service_type_id,
                postal_code="H7A1A1",
                provider_name="manual",
                q="deep cleaning",
            ),
        )

        self.assertEqual(response.status_code, 200)
        provider_cards = response.context["providers"]
        self.assertEqual(len(provider_cards), 20)
        self.assertEqual(response.context["provider_name"], "manual")
        self.assertTrue(all("manual" in card.card_display_name.lower() for card in provider_cards))
        self.assertTrue(
            all(card.provider_id != no_subservice_provider.provider_id for card in provider_cards)
        )
        self.assertTrue(
            all(card.provider_id != other_service_provider.provider_id for card in provider_cards)
        )
        self.assertNotContains(response, "Manual No Deep")
        self.assertNotContains(response, "Manual Other Service")
        self.assertContains(response, matching_names[0])
        self.assertContains(response, 'name="provider_name"', html=False)
        self.assertContains(response, 'value="manual"', html=False)

    def test_marketplace_provider_name_filter_prioritizes_stronger_name_matches(self):
        client_obj = Client.objects.create(
            first_name="Provider",
            last_name="Name Priority",
            phone_number="5550000428",
            email="marketplace.provider.priority@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="14 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        cleaning = ServiceType.objects.create(
            name="Provider Name Priority Cleaning",
            description="Provider Name Priority Cleaning",
        )

        infix_provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Team Manual Experts",
            legal_name="Team Manual Experts",
            business_registration_number="REG-PRIORITY-001",
            contact_first_name="Team",
            contact_last_name="Manual",
            phone_number="5550000429",
            email="provider.priority.infix@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="15 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area="Laval",
        )
        ProviderServiceArea.objects.create(
            provider=infix_provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=infix_provider,
            service_type=cleaning,
            custom_name="Infix Manual Cleaning",
            billing_unit="fixed",
            price_cents=8000,
            is_active=True,
        )

        exact_provider = Provider.objects.create(
            provider_type=Provider.TYPE_COMPANY,
            company_name="Manual",
            legal_name="Manual",
            business_registration_number="REG-PRIORITY-002",
            contact_first_name="Manual",
            contact_last_name="Exact",
            phone_number="5550000430",
            email="provider.priority.exact@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="16 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area="Laval",
        )
        ProviderServiceArea.objects.create(
            provider=exact_provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=exact_provider,
            service_type=cleaning,
            custom_name="Exact Manual Cleaning",
            billing_unit="fixed",
            price_cents=15000,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(
                service_type=cleaning.service_type_id,
                postal_code="H7A1A1",
                provider_name="manual",
            ),
        )

        self.assertEqual(response.status_code, 200)
        provider_cards = response.context["providers"]
        self.assertEqual(provider_cards[0].provider_id, exact_provider.provider_id)
        self.assertEqual(provider_cards[0].card_display_name, "Manual")
        self.assertEqual(provider_cards[1].provider_id, infix_provider.provider_id)

    def test_marketplace_services_mode_preserves_provider_name_in_form_and_links(self):
        self._create_verified_client()
        service_type = ServiceType.objects.create(
            name="Provider Name Preserve Service",
            description="Provider Name Preserve Service",
            is_active=True,
        )
        self._create_provider_with_offer(
            email="provider.name.preserve@test.local",
            phone_number="5550000490",
            city="Laval",
            service_type=service_type,
            price_cents=12000,
            postal_prefix="H7A",
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            {
                "provider_name": "manual",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "services")
        self.assertContains(
            response,
            'type="hidden" name="provider_name" value="manual"',
            html=False,
        )
        self.assertContains(
            response,
            f'&amp;provider_name=manual"',
            html=False,
        )
        self.assertContains(response, service_type.name)


class RequestCreateComplianceTests(TestCase):
    def setUp(self):
        super().setUp()
        self.geocode_address_patcher = patch("ui.views.geocode_address", return_value=None)
        self.geocode_address_patcher.start()
        self.addCleanup(self.geocode_address_patcher.stop)

    def _login_client(self, client_obj):
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

    def _scheduled_marketplace_query(self, **kwargs):
        params = {"service_timing": "scheduled"}
        params.update(kwargs)
        return params

    def _create_provider_with_offer(
        self,
        *,
        email,
        phone_number,
        city,
        service_type,
        price_cents,
        provider_type="self_employed",
        postal_prefix=None,
    ):
        display_first_name = email.split(".", 1)[0].split("@", 1)[0].title()
        provider = Provider.objects.create(
            provider_type=provider_type,
            legal_name="Provider Legal" if provider_type == "self_employed" else "",
            company_name="Provider Company" if provider_type == "company" else None,
            business_registration_number="REG-001" if provider_type == "company" else "",
            contact_first_name=display_first_name,
            contact_last_name="Offer",
            phone_number=phone_number,
            email=email,
            province="QC",
            city=city,
            postal_code="H1A1A1",
            address_line1="100 Provider St",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area=city,
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city=city,
            province="QC",
            postal_prefix=postal_prefix,
            is_active=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name=f"{service_type.name} Service",
            billing_unit="fixed",
            price_cents=price_cents,
            is_active=True,
        )
        return provider

    def _create_verified_client(self):
        client_obj = Client.objects.create(
            first_name="Compliant",
            last_name="Client",
            phone_number="5550000500",
            email="compliance.client@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="50 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)
        return client_obj

    def _create_non_compliant_offer(self):
        service_type = ServiceType.objects.create(name="Electrical", description="Electrical")
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Non Compliant Provider",
            contact_first_name="Non",
            contact_last_name="Compliant",
            phone_number="5550000501",
            email="non.compliant.provider@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="51 Provider St",
            service_area="Laval",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Electrical Service",
            billing_unit="fixed",
            price_cents=15000,
            is_active=True,
        )
        RequiredCertification.objects.create(
            service_type=service_type,
            province="QC",
            requires_certificate=True,
            certificate_type="RBQ",
        )
        return provider, service_type

    def test_marketplace_marks_non_compliant_offer_as_blocked(self):
        self._create_verified_client()
        provider, service_type = self._create_non_compliant_offer()

        response = self.client.get(
            reverse("ui:marketplace_search"),
            self._scheduled_marketplace_query(service_type=service_type.service_type_id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compliance required")
        self.assertNotContains(
            response,
            f'href="{reverse("ui:request_create", args=[provider.pk])}?service_type_id={service_type.service_type_id}"',
        )

    def test_marketplace_search_preserves_service_timing_in_provider_link(self):
        self._create_verified_client()
        service_type = ServiceType.objects.create(name="Timed Search", description="Timed Search")
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Timed Provider",
            contact_first_name="Timed",
            contact_last_name="Provider",
            phone_number="5550000520",
            email="timed.provider@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="52 Provider St",
            service_area="Laval",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Timed Service",
            billing_unit="fixed",
            price_cents=15000,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            {
                "service_type": service_type.service_type_id,
                "service_timing": "scheduled",
                "postal_code": "H7A1A1",
                "city": "Laval",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            (
                f'href="{reverse("ui:request_create", args=[provider.pk])}'
                f'?service_type_id={service_type.service_type_id}'
                f'&amp;service_timing=scheduled'
                f'&amp;postal_code=H7A1A1'
                f'&amp;city=Laval"'
            ),
            html=False,
        )

    def test_marketplace_search_preserves_provider_name_in_provider_link(self):
        self._create_verified_client()
        service_type = ServiceType.objects.create(
            name="Provider Name Link Search",
            description="Provider Name Link Search",
        )
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Manual Provider",
            contact_first_name="Manual",
            contact_last_name="Provider",
            phone_number="5550000522",
            email="provider.name.link@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="54 Provider St",
            service_area="Laval",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Manual Link Service",
            billing_unit="fixed",
            price_cents=15000,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            {
                "service_type": service_type.service_type_id,
                "service_timing": "scheduled",
                "postal_code": "H7A1A1",
                "city": "Laval",
                "provider_name": "manual",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            (
                f'href="{reverse("ui:request_create", args=[provider.pk])}'
                f'?service_type_id={service_type.service_type_id}'
                f'&amp;service_timing=scheduled'
                f'&amp;postal_code=H7A1A1'
                f'&amp;city=Laval'
                f'&amp;provider_name=manual"'
            ),
            html=False,
        )

    def test_marketplace_search_preserves_profile_address_in_provider_link(self):
        self._create_verified_client()
        service_type = ServiceType.objects.create(
            name="Profile Address Search",
            description="Profile Address Search",
        )
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Profile Address Provider",
            contact_first_name="Profile",
            contact_last_name="Provider",
            phone_number="5550000521",
            email="profile.address.provider@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="53 Provider St",
            service_area="Laval",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            postal_prefix="H7A",
            is_active=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type,
            custom_name="Profile Address Service",
            billing_unit="fixed",
            price_cents=15000,
            is_active=True,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            {
                "service_type": service_type.service_type_id,
                "service_timing": "scheduled",
                "use_profile_address": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            (
                f'href="{reverse("ui:request_create", args=[provider.pk])}'
                f'?service_type_id={service_type.service_type_id}'
                f'&amp;service_timing=scheduled'
                f'&amp;province=QC'
                f'&amp;postal_code=H7A1A1'
                f'&amp;city=Laval"'
            ),
            html=False,
        )

    def test_marketplace_search_requires_service_timing_before_showing_providers(self):
        client_obj = Client.objects.create(
            first_name="Timing",
            last_name="Required",
            phone_number="5550000422",
            email="marketplace.timing.required@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="8 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        hvac = ServiceType.objects.create(name="Timing HVAC", description="Timing HVAC")
        provider = self._create_provider_with_offer(
            email="timing.provider@test.local",
            phone_number="5550000423",
            city="Laval",
            service_type=hvac,
            price_cents=12500,
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            {"service_type": hvac.service_type_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "services")
        self.assertTrue(response.context["timing_required"])
        self.assertNotContains(
            response,
            reverse("ui:request_create", args=[provider.pk]),
        )
        self.assertContains(response, "Choose when you need the service before searching providers.")

    def test_marketplace_search_shows_emergency_cta_without_provider_cards(self):
        client_obj = Client.objects.create(
            first_name="Emergency",
            last_name="Client",
            phone_number="5550000424",
            email="marketplace.emergency@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A1A1",
            address_line1="9 Client St",
            is_phone_verified=True,
            accepts_terms=True,
            profile_completed=True,
        )
        self._login_client(client_obj)

        hvac = ServiceType.objects.create(name="Emergency HVAC", description="Emergency HVAC")
        provider = self._create_provider_with_offer(
            email="emergency.provider@test.local",
            phone_number="5550000425",
            city="Laval",
            service_type=hvac,
            price_cents=13000,
            postal_prefix="H7A",
        )

        response = self.client.get(
            reverse("ui:marketplace_search"),
            {
                "service_type": hvac.service_type_id,
                "service_timing": "emergency",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "emergency")
        self.assertContains(response, "Emergency request")
        self.assertContains(response, "Continue to emergency request")
        self.assertContains(
            response,
            (
                f'href="{reverse("ui:providers_nearby")}'
                f'?fsa=H7A'
                f'&amp;postal_code=H7A1A1'
                f'&amp;city=Laval'
                f'&amp;province=QC'
                f'&amp;service_type={hvac.service_type_id}'
                f'&amp;service_timing=emergency"'
            ),
            html=False,
        )
        self.assertNotContains(
            response,
            reverse("ui:request_create", args=[provider.pk]),
        )
        self.assertNotContains(response, 'provider-card provider-card--compact', html=False)

    def test_request_create_blocks_post_when_offer_is_not_compliant(self):
        self._create_verified_client()
        provider, service_type = self._create_non_compliant_offer()

        response = self.client.post(
            reverse("ui:request_create", args=[provider.pk]),
            data={
                "service_type_id": str(service_type.service_type_id),
                "service_type": str(service_type.service_type_id),
                "job_mode": Job.JobMode.ON_DEMAND,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "This service cannot be requested until provider compliance is complete.",
        )
        self.assertEqual(Job.objects.count(), 0)
