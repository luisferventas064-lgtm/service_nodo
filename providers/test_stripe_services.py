from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import TestCase

from providers.models import Provider
from providers.stripe_services import (
    create_stripe_connected_account,
    generate_stripe_onboarding_link,
)


class ProviderStripeServicesTests(TestCase):
    def _make_provider(self, *, email: str, stripe_account_id=None) -> Provider:
        return Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Stripe",
            contact_last_name="Provider",
            phone_number="555-900-0001",
            email=email,
            stripe_account_id=stripe_account_id,
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Stripe St",
        )

    @patch("providers.stripe_services.get_stripe")
    def test_create_stripe_connected_account_is_idempotent_when_existing(self, get_stripe_mock):
        provider = self._make_provider(
            email="provider.existing.stripe@test.local",
            stripe_account_id="acct_existing_123",
        )

        account_id = create_stripe_connected_account(provider)

        self.assertEqual(account_id, "acct_existing_123")
        get_stripe_mock.assert_not_called()

    @patch("providers.stripe_services.get_stripe")
    def test_create_stripe_connected_account_creates_and_persists(self, get_stripe_mock):
        provider = self._make_provider(
            email="provider.new.stripe@test.local",
            stripe_account_id=None,
        )

        stripe_mock = MagicMock()
        account_mock = MagicMock()
        account_mock.id = "acct_new_123"
        payload = {
            "details_submitted": True,
            "charges_enabled": True,
            "payouts_enabled": True,
        }
        account_mock.get.side_effect = lambda key, default=None: payload.get(key, default)
        stripe_mock.Account.create.return_value = account_mock
        get_stripe_mock.return_value = stripe_mock

        account_id = create_stripe_connected_account(provider)

        self.assertEqual(account_id, "acct_new_123")
        provider.refresh_from_db()
        self.assertEqual(provider.stripe_account_id, "acct_new_123")
        self.assertEqual(provider.stripe_account_status, "submitted")
        self.assertTrue(provider.stripe_onboarding_completed)
        self.assertTrue(provider.stripe_charges_enabled)
        self.assertTrue(provider.stripe_payouts_enabled)
        self.assertIsNotNone(provider.stripe_details_submitted_at)
        stripe_mock.Account.create.assert_called_once_with(
            type="express",
            country="CA",
            email="provider.new.stripe@test.local",
            capabilities={"transfers": {"requested": True}},
        )

    def test_generate_stripe_onboarding_link_requires_connected_account(self):
        provider = self._make_provider(
            email="provider.noacct.stripe@test.local",
            stripe_account_id=None,
        )

        with self.assertRaisesRegex(ValueError, "Provider has no Stripe account"):
            generate_stripe_onboarding_link(provider)

    @patch("providers.stripe_services.get_stripe")
    def test_generate_stripe_onboarding_link_returns_url(self, get_stripe_mock):
        provider = self._make_provider(
            email="provider.link.stripe@test.local",
            stripe_account_id="acct_link_123",
        )

        stripe_mock = MagicMock()
        link_mock = MagicMock()
        link_mock.url = "https://connect.stripe.test/onboarding/link"
        stripe_mock.AccountLink.create.return_value = link_mock
        get_stripe_mock.return_value = stripe_mock

        url = generate_stripe_onboarding_link(provider)

        self.assertEqual(url, "https://connect.stripe.test/onboarding/link")
        stripe_mock.AccountLink.create.assert_called_once_with(
            account="acct_link_123",
            refresh_url=settings.STRIPE_ONBOARDING_REFRESH_URL,
            return_url=settings.STRIPE_ONBOARDING_RETURN_URL,
            type="account_onboarding",
        )
