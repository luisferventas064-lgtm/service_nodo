from django.core.management.base import BaseCommand
from django.db import transaction

from settlements.services import generate_weekly_settlements


class Command(BaseCommand):
    help = "Generate weekly provider settlements (previous Monday-Sunday)"

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("Starting weekly settlement generation..."))

        try:
            with transaction.atomic():
                settlements = generate_weekly_settlements()

            if not settlements:
                self.stdout.write(self.style.SUCCESS("No settlements generated."))
                return

            self.stdout.write(
                self.style.SUCCESS(
                    f"{len(settlements)} settlement(s) generated successfully."
                )
            )

            for settlement in settlements:
                self.stdout.write(
                    f" - Provider {settlement.provider_id} | "
                    f"{settlement.period_start} -> {settlement.period_end} | "
                    f"Net: {settlement.total_net_provider_cents}"
                )

        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Error: {str(exc)}"))
            raise
