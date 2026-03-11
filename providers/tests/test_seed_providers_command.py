from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from providers.management.commands.seed_providers import (
    POSTAL_AREAS,
    SEED_EMAIL_PREFIX,
    SEED_SERVICE_TYPES,
)
from providers.models import Provider, ProviderServiceArea


class SeedProvidersCommandTests(TestCase):
    def test_seed_providers_creates_ranked_seed_data_and_reset_replaces_it(self):
        output = StringIO()

        call_command(
            "seed_providers",
            count=5,
            seed=123,
            reset=True,
            stdout=output,
        )

        providers = Provider.objects.filter(email__startswith=SEED_EMAIL_PREFIX).order_by("provider_id")
        self.assertEqual(providers.count(), 5)
        self.assertIn("Seeded 5 providers", output.getvalue())

        valid_prefixes = {area["postal_prefix"] for area in POSTAL_AREAS}
        for provider in providers:
            self.assertGreater(provider.hybrid_score, 0.0)
            self.assertGreater(provider.base_dispatch_score, 0.0)
            self.assertEqual(provider.services.filter(is_active=True).count(), len(SEED_SERVICE_TYPES))
            self.assertTrue(ProviderServiceArea.objects.filter(provider=provider, is_active=True).exists())
            self.assertTrue(provider.metrics.jobs_accepted >= provider.metrics.jobs_completed)
            self.assertIn(
                ProviderServiceArea.objects.filter(provider=provider).first().postal_prefix,
                valid_prefixes,
            )

        output = StringIO()
        call_command(
            "seed_providers",
            count=3,
            seed=123,
            reset=True,
            stdout=output,
        )

        providers = Provider.objects.filter(email__startswith=SEED_EMAIL_PREFIX)
        self.assertEqual(providers.count(), 3)
        self.assertIn("Deleted existing seed providers", output.getvalue())
