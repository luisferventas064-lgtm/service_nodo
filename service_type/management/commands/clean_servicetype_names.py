"""
Management command to clean ServiceType records that contain trailing hash/token artifacts.

Purpose:
  Identify and remove trailing 8-char hex tokens (e.g., "Nettoyage 618eb1c2" -> "Nettoyage")
  from ServiceType names. Supports dry-run mode and optional population of localized fields.

Examples:
  # Dry-run: show what would be cleaned
  python manage.py clean_servicetype_names --dry-run

  # Apply cleaning and report results
  python manage.py clean_servicetype_names

  # Clean and try to populate empty localized fields from base name
  python manage.py clean_servicetype_names --populate-defaults
"""

import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from service_type.models import ServiceType


class Command(BaseCommand):
    help = "Clean ServiceType names by removing trailing hash/token artifacts (e.g., 'Nettoyage 618eb1c2' -> 'Nettoyage')."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without applying them.",
        )
        parser.add_argument(
            "--populate-defaults",
            action="store_true",
            help="If name_en/name_fr/name_es are empty after cleaning base name, populate them from the cleaned name.",
        )
        parser.add_argument(
            "--merge-duplicates",
            action="store_true",
            help="When multiple dirty records clean to same name, keep first (lowest ID), delete others, merge their localized fields.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show detailed info for each updated record.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        populate_defaults = options["populate_defaults"]
        merge_duplicates = options["merge_duplicates"]
        verbose = options["verbose"]

        # Regex pattern to match trailing hash/token (8 hex chars)
        trailing_token_re = re.compile(r"^(?P<label>.+?)\s+[0-9a-f]{8}$", re.IGNORECASE)

        # Find all ServiceType records that need cleaning
        dirty_records = []
        for st in ServiceType.objects.all():
            if trailing_token_re.match(st.name):
                dirty_records.append(st)

        if not dirty_records:
            self.stdout.write(self.style.SUCCESS("✓ No dirty ServiceType names found. Catalog is clean!"))
            return

        # Group by cleaned name to detect potential collisions
        cleaned_groups = {}
        for st in dirty_records:
            match = trailing_token_re.match(st.name)
            if match:
                cleaned_name = match.group("label").strip()
                if cleaned_name not in cleaned_groups:
                    cleaned_groups[cleaned_name] = []
                cleaned_groups[cleaned_name].append(st)

        # Check for multi-dirty collisions
        multi_dirty_groups = {k: v for k, v in cleaned_groups.items() if len(v) > 1}

        if multi_dirty_groups and not merge_duplicates:
            self.stdout.write(
                self.style.ERROR("\n⚠ ERROR: Multiple dirty records would merge to same name:")
            )
            for clean_name, records in sorted(multi_dirty_groups.items()):
                self.stdout.write(f"\n  '{clean_name}': {len(records)} registros sucios")
                for st in sorted(records, key=lambda x: x.service_type_id):
                    self.stdout.write(f"    - ID {st.service_type_id}: {st.name}")

            self.stdout.write(
                self.style.WARNING(
                    "\n\nTo resolve, use: python manage.py clean_servicetype_names --merge-duplicates\n"
                    "This will: keep lowest ID, delete duplicates, merge localized fields."
                )
            )
            raise CommandError("Cannot clean with duplicate collisions. Use --merge-duplicates flag.")

        self.stdout.write(
            self.style.WARNING(f"Found {len(dirty_records)} ServiceType records with trailing tokens:")
        )

        updates = []
        deletes = []  # List of (keeper_st, duplicate_st) tuples
        keepers_by_cleaned_name = {}

        # If merge_duplicates, pre-identify keepers before processing updates
        if merge_duplicates and multi_dirty_groups:
            self.stdout.write(self.style.WARNING("\n[MERGE MODE] Will consolidate duplicates:"))
            for clean_name, records in sorted(multi_dirty_groups.items()):
                sorted_records = sorted(records, key=lambda x: x.service_type_id)
                keeper = sorted_records[0]
                duplicates = sorted_records[1:]

                keepers_by_cleaned_name[clean_name] = keeper

                self.stdout.write(f"\n  '{clean_name}':")
                self.stdout.write(f"    Keep ID {keeper.service_type_id}")
                for dup in duplicates:
                    self.stdout.write(f"    Delete ID {dup.service_type_id} (merge localized fields)")
                    deletes.append((keeper, dup))

        for st in dirty_records:
            match = trailing_token_re.match(st.name)
            if match:
                cleaned_name = match.group("label").strip()
                self.stdout.write(f"  {st.service_type_id}: '{st.name}' -> '{cleaned_name}'")

                updates.append({
                    "st": st,
                    "cleaned_name": cleaned_name,
                    "original_name": st.name,
                })

        if dry_run:
            self.stdout.write(
                self.style.NOTICE(
                    f"\n[DRY-RUN] Would update {len(updates) - len(deletes)} records"
                    f"{f' and delete {len(deletes)} duplicates' if deletes else ''}. "
                    "Use without --dry-run to apply."
                )
            )
            return

        # Apply changes
        if not self._confirm_update(len(updates) - len(deletes), len(deletes)):
            self.stdout.write(self.style.WARNING("Cancelled."))
            return

        # Mark duplicates for exclusion from updates
        duplicate_st_ids = {dup.service_type_id for keeper, dup in deletes}

        with transaction.atomic():
            # IMPORTANT: Delete duplicates FIRST using direct SQL before updating keepers
            # This avoids UNIQUE constraint violations mid-transaction
            if deletes:
                self.stdout.write(self.style.WARNING("\n[DELETE PHASE] Removing duplicates..."))
                from django.db import connection
                
                duplicate_ids = [dup.service_type_id for keeper, dup in deletes]
                
                # First, reassign foreign keys to keepers
                keepers_map = {dup.service_type_id: keeper.service_type_id for keeper, dup in deletes}
                
                # Reassign jobs
                from jobs.models import Job
                for dup_id, keeper_id in keepers_map.items():
                    reassigned = Job.objects.filter(service_type_id=dup_id).update(service_type_id=keeper_id)
                    if reassigned and verbose:
                        self.stdout.write(self.style.WARNING(f"  -> Reassigned {reassigned} Job(s) from ID {dup_id} to ID {keeper_id}"))
                
                # Reassign provider services
                from providers.models import ProviderService
                for dup_id, keeper_id in keepers_map.items():
                    reassigned = ProviderService.objects.filter(service_type_id=dup_id).update(service_type_id=keeper_id)
                    if reassigned and verbose:
                        self.stdout.write(self.style.WARNING(f"  -> Reassigned {reassigned} ProviderService(s) from ID {dup_id} to ID {keeper_id}"))
                
                # Merge localized fields from duplicates into keepers BEFORE deleting
                for keeper, dup_st in deletes:
                    if not keeper.name_en and dup_st.name_en:
                        keeper.name_en = dup_st.name_en
                    if not keeper.name_fr and dup_st.name_fr:
                        keeper.name_fr = dup_st.name_fr
                    if not keeper.name_es and dup_st.name_es:
                        keeper.name_es = dup_st.name_es
                    keeper.save(update_fields=['name_en', 'name_fr', 'name_es'])
                
                # Delete duplicates using raw SQL for certainty
                placeholders = ','.join(['%s'] * len(duplicate_ids))
                with connection.cursor() as cursor:
                    cursor.execute(f"DELETE FROM service_type WHERE service_type_id IN ({placeholders})", duplicate_ids)
                    if verbose:
                        self.stdout.write(self.style.WARNING(f"  -> Deleted {len(duplicate_ids)} duplicate records via SQL"))

            # UPDATE PHASE: Now that duplicates are gone, update keepers safely
            self.stdout.write(self.style.WARNING("\n[UPDATE PHASE] Cleaning keeper names..."))
            updated_count = 0
            for update_info in updates:
                # Re-fetch the object to ensure we have latest state from DB
                try:
                    st = ServiceType.objects.get(service_type_id=update_info["st"].service_type_id)
                except ServiceType.DoesNotExist:
                    # This record was deleted (it was a duplicate)
                    continue

                cleaned_name = update_info["cleaned_name"]

                # Update base name
                st.name = cleaned_name

                # Optionally populate localized fields if empty
                if populate_defaults:
                    if not st.name_en:
                        st.name_en = cleaned_name
                    if not st.name_fr:
                        st.name_fr = cleaned_name
                    if not st.name_es:
                        st.name_es = cleaned_name

                st.save()
                updated_count += 1

                if verbose:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Updated {st.service_type_id}: name='{cleaned_name}'"
                        )
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Successfully cleaned {updated_count} ServiceType records"
                f"{f' and deleted {len(deletes)} duplicates' if deletes else ''}."
            )
        )

    def _confirm_update(self, count_updates: int, count_deletes: int = 0) -> bool:
        """Ask user for confirmation before updating."""
        msg = f"About to update {count_updates} records"
        if count_deletes:
            msg += f" and delete {count_deletes} duplicates (merging localized data)"
        msg += ". Proceed? [y/N]: "
        response = input(msg)
        return response.lower() in ("y", "yes")
