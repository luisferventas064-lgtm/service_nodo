from django.core.management.base import BaseCommand

from settlements.services import auto_resolve_expired_provider_response


class Command(BaseCommand):
    help = "Auto-resolve disputes where provider 24h response window has expired."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate auto-resolution without mutating disputes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        processed = auto_resolve_expired_provider_response(dry_run=dry_run)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Expired disputes eligible for auto-resolve: {len(processed)}"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Auto-resolved disputes: {len(processed)}"
                )
            )

        if processed:
            self.stdout.write(f"IDs: {processed}")
