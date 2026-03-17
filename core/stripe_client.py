import stripe
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def get_stripe():
    api_key = getattr(settings, "STRIPE_SECRET_KEY", None)
    if not api_key:
        raise ImproperlyConfigured(
            "Stripe secret key not configured. Set STRIPE_SECRET_KEY_TEST or STRIPE_SECRET_KEY."
        )

    stripe.api_key = api_key
    return stripe
