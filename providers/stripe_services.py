from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.stripe_client import get_stripe
from providers.models import Provider


@transaction.atomic
def create_stripe_connected_account(provider: Provider) -> str:
    locked_provider = Provider.objects.select_for_update().get(pk=provider.pk)
    if locked_provider.stripe_account_id:
        return locked_provider.stripe_account_id

    stripe = get_stripe()
    account = stripe.Account.create(
        type="express",
        country="CA",
        email=locked_provider.email,
        capabilities={
            "transfers": {"requested": True},
        },
    )

    details_submitted = bool(account.get("details_submitted"))
    locked_provider.stripe_account_id = account.id
    locked_provider.stripe_account_status = (
        "submitted" if details_submitted else "pending"
    )
    locked_provider.stripe_onboarding_completed = details_submitted
    locked_provider.stripe_charges_enabled = bool(account.get("charges_enabled"))
    locked_provider.stripe_payouts_enabled = bool(account.get("payouts_enabled"))
    locked_provider.stripe_details_submitted_at = (
        timezone.now() if details_submitted else None
    )
    locked_provider.save(
        update_fields=[
            "stripe_account_id",
            "stripe_account_status",
            "stripe_onboarding_completed",
            "stripe_charges_enabled",
            "stripe_payouts_enabled",
            "stripe_details_submitted_at",
        ]
    )
    return locked_provider.stripe_account_id


def generate_stripe_onboarding_link(provider: Provider) -> str:
    provider_ref = Provider.objects.only("provider_id", "stripe_account_id").get(
        pk=provider.pk
    )
    if not provider_ref.stripe_account_id:
        raise ValueError("Provider has no Stripe account")

    stripe = get_stripe()
    link = stripe.AccountLink.create(
        account=provider_ref.stripe_account_id,
        refresh_url=settings.STRIPE_ONBOARDING_REFRESH_URL,
        return_url=settings.STRIPE_ONBOARDING_RETURN_URL,
        type="account_onboarding",
    )
    return link.url
