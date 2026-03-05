from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.hashers import check_password
from django.contrib.auth.hashers import make_password
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Client
from jobs.models import Job
from providers.models import Provider, ProviderService, ProviderServiceArea
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

    def test_invalid_role_login_shows_error(self):
        response = self.client.post(
            reverse("ui:login_client"),
            data={
                "identifier": "missing@test.local",
                "password": "wrong-pass",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid credentials.")

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
    def test_client_profile_alias_redirects_to_client_profile(self):
        client_obj = Client.objects.create(
            first_name="Alias",
            last_name="Client",
            phone_number="5550000198",
            email="alias.client@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="198 Client St",
            is_phone_verified=True,
            profile_completed=True,
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
        self.assertEqual(client.jobs.first().selected_provider_id, provider.pk)

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
        self.assertContains(response, "Client:")
        self.assertContains(response, "Luis Garcia")
        self.assertContains(response, "Service Type:")
        self.assertContains(response, "Cleaning Service")
        self.assertContains(response, "Mode:")
        self.assertContains(response, "Scheduled")
        self.assertContains(response, "Date:")
        self.assertContains(response, "Time:")
        self.assertContains(response, "Address:")
        self.assertContains(response, "921 100 e avenue, Laval, QC, H7W4A2")

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


class MarketplaceSearchViewTests(TestCase):
    def _login_client(self, client_obj):
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

    def _create_provider_with_offer(
        self,
        *,
        email,
        phone_number,
        city,
        service_type,
        price_cents,
        provider_type="self_employed",
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
            {"service_type": hvac.service_type_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, city_provider.contact_first_name)
        self.assertNotContains(response, province_provider.contact_first_name)
        self.assertContains(response, "HVAC")

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
            {"service_type": hvac.service_type_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, province_provider.contact_first_name)
        self.assertContains(response, "No providers found.")
        self.assertContains(response, "HVAC")


class RequestCreateComplianceTests(TestCase):
    def _login_client(self, client_obj):
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

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
            {"service_type": service_type.service_type_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compliance required")
        self.assertNotContains(
            response,
            f'href="{reverse("ui:request_create", args=[provider.pk])}?service_type_id={service_type.service_type_id}"',
        )

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
