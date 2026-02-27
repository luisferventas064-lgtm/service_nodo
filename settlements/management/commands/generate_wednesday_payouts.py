from django.core.management.base import BaseCommand

from settlements.services import generate_wednesday_payouts


class Command(BaseCommand):
    help = "Execute scheduled Wednesday payouts for closed settlements."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate payouts without marking settlements as paid.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        processed = generate_wednesday_payouts(dry_run=dry_run)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Settlements eligible for payout: {len(processed)}"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Settlements paid: {len(processed)}"
                )
            )

        if processed:
            self.stdout.write(f"IDs: {processed}")
