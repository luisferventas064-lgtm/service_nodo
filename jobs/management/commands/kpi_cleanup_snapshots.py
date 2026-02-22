from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import KpiSnapshot


class Command(BaseCommand):
    help = "Delete old KPI snapshots (retention)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep-days",
            type=int,
            default=30,
            help="Retention window in days (default: 30).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without deleting.",
        )

    def handle(self, *args, **options):
        keep_days = int(options["keep_days"])
        dry_run = bool(options["dry_run"])

        cutoff = timezone.now() - timedelta(days=keep_days)

        qs = KpiSnapshot.objects.filter(created_at__lt=cutoff).order_by("created_at")
        count = qs.count()

        if dry_run:
            self.stdout.write(
                f"DRY RUN: would delete {count} snapshots older than {keep_days} days (cutoff={cutoff})."
            )
            for s in qs[:20]:
                self.stdout.write(
                    f"  id={s.id} created_at={s.created_at} window_hours={s.window_hours}"
                )
            if count > 20:
                self.stdout.write(f"  ... and {count-20} more")
            return

        deleted, _ = qs.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"OK deleted {deleted} rows older than {keep_days} days (cutoff={cutoff})"
            )
        )
