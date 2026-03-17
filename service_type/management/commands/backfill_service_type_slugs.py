"""
Management command: backfill_service_type_slugs

Populates ServiceType.slug from name_en (fallback: name) using Django's
slugify. Skips rows that already have a slug. Aborts if it detects
a collision (two rows produce the same slug) and prints a clear report
so the operator can resolve the conflict manually before re-running.

Test-data rows (names with hex-hash suffixes like "Service d533049c" or
"Nettoyage 8cc77f40", and rows matching "Offers Test") are silently
skipped — they should not exist in production and must not receive slugs.

Usage:
    python manage.py backfill_service_type_slugs
    python manage.py backfill_service_type_slugs --dry-run
"""

import re

from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from service_type.models import ServiceType

# Matches names that are clearly test-data artifacts:
#   "Service <hex8>"  "Nettoyage <hex8>"  "Offers Test …"
_TEST_DATA_RE = re.compile(
    r"^(service|nettoyage)\s+[0-9a-f]{6,}$|^offers\s+test\b",
    re.IGNORECASE,
)

# Curated mapping: service_type_id → slug
# Add entries here for rows that need a specific, hand-crafted slug
# (e.g. names that are non-English or produce ugly auto-slugs).
MANUAL_OVERRIDES: dict[int, str] = {
    # Example:
    # 7: "home-cleaning",
}


class Command(BaseCommand):
    help = "Backfill ServiceType.slug from name_en/name using slugify."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be written without saving.",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]

        rows = list(ServiceType.objects.all().order_by("service_type_id"))
        pending = [r for r in rows if not r.slug]

        if not pending:
            self.stdout.write(self.style.SUCCESS("All rows already have a slug. Nothing to do."))
            return

        # Build proposed slug map (skip test-data rows)
        proposed: dict[int, str] = {}
        skipped_test: list[str] = []
        for obj in pending:
            source = (obj.name_en or obj.name or "").strip()

            if _TEST_DATA_RE.search(source):
                skipped_test.append(f"  pk={obj.service_type_id} {obj.name!r} [test-data, skipped]")
                continue

            if obj.service_type_id in MANUAL_OVERRIDES:
                candidate = MANUAL_OVERRIDES[obj.service_type_id]
            else:
                candidate = slugify(source)

            if not candidate:
                self.stderr.write(
                    self.style.ERROR(
                        f"Row pk={obj.service_type_id} name={obj.name!r} produced an empty slug. "
                        "Add a manual override in MANUAL_OVERRIDES and re-run."
                    )
                )
                raise CommandError("Empty slug detected. Aborting.")

            proposed[obj.service_type_id] = candidate

        if skipped_test:
            self.stdout.write(self.style.WARNING(f"Skipping {len(skipped_test)} test-data row(s):"))
            for msg in skipped_test:
                self.stdout.write(msg)

        if not proposed:
            self.stdout.write(self.style.SUCCESS("No eligible rows to update after filtering."))
            return

            proposed[obj.service_type_id] = candidate

        # Check for collisions within new slugs
        seen_slugs: dict[str, int] = {}
        collisions: list[str] = []
        for pk, slug in proposed.items():
            if slug in seen_slugs:
                collisions.append(
                    f"  slug={slug!r} → pk={seen_slugs[slug]} and pk={pk}"
                )
            else:
                seen_slugs[slug] = pk

        # Also check against already-committed slugs in DB
        existing_slugs = {
            r.slug: r.service_type_id
            for r in rows
            if r.slug and r.service_type_id not in proposed
        }
        for pk, slug in proposed.items():
            if slug in existing_slugs:
                collisions.append(
                    f"  slug={slug!r} → existing pk={existing_slugs[slug]} collides with pending pk={pk}"
                )

        if collisions:
            self.stderr.write(self.style.ERROR("Slug collisions detected. Aborting:"))
            for msg in collisions:
                self.stderr.write(msg)
            raise CommandError(
                "Fix collisions by adding entries to MANUAL_OVERRIDES in the command file and re-run."
            )

        # Report / apply — only iterate over rows that passed filtering
        eligible = [obj for obj in pending if obj.service_type_id in proposed]
        for obj in eligible:
            slug = proposed[obj.service_type_id]
            if dry_run:
                self.stdout.write(f"  [dry-run] pk={obj.service_type_id} {obj.name!r} → {slug!r}")
            else:
                obj.slug = slug
                obj.save(update_fields=["slug"])
                self.stdout.write(f"  pk={obj.service_type_id} {obj.name!r} → {slug!r}")

        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry-run complete. {len(eligible)} row(s) would be updated."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Done. {len(eligible)} row(s) updated."))
