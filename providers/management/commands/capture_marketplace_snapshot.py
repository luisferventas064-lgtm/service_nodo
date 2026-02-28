import json

from django.core.management.base import BaseCommand

from providers.models import MarketplaceAnalyticsSnapshot
from providers.services_analytics import marketplace_analytics_snapshot


class Command(BaseCommand):
    help = "Capture the current marketplace analytics snapshot."

    def handle(self, *args, **options):
        snapshot = marketplace_analytics_snapshot()
        record = MarketplaceAnalyticsSnapshot.objects.create(
            snapshot=json.dumps(snapshot, separators=(",", ":")),
        )

        total_providers = snapshot.get("global", {}).get("total_providers", 0)
        self.stdout.write(
            self.style.SUCCESS(
                "Captured marketplace analytics snapshot "
                f"#{record.marketplace_analytics_snapshot_id} "
                f"at {record.captured_at.isoformat()} "
                f"({total_providers} providers)."
            )
        )
