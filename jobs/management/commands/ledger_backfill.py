from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from jobs.ledger import rebuild_platform_ledger_for_job
from jobs.models import Job


@dataclass
class Stats:
    scanned: int = 0
    processed: int = 0
    rebuilt: int = 0
    skipped: int = 0
    errors: int = 0


class Command(BaseCommand):
    help = "Backfill missing PlatformLedgerEntry for jobs (safe batch). Creates ledger via rebuild; does NOT finalize."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200, help="Max jobs to scan/process.")
        parser.add_argument(
            "--since-days",
            type=int,
            default=None,
            help="Only consider jobs updated/created in last N days.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Show what would be rebuilt, but do not write.")
        parser.add_argument(
            "--run-id-prefix",
            type=str,
            default="BACKFILL_LEDGER",
            help="Prefix for run_id.",
        )
        parser.add_argument(
            "--reason",
            type=str,
            default="backfill_missing_ledger",
            help="Reason string saved on rebuild.",
        )
        parser.add_argument(
            "--only-status",
            type=str,
            default=None,
            help="Optional: only jobs with this status (e.g. posted).",
        )
        parser.add_argument("--exclude-status", type=str, default=None, help="Optional: exclude jobs with this status.")
        parser.add_argument("--job-ids", type=str, default=None, help="Optional: comma list, e.g. 17,20,21")

    def handle(self, *args, **opts):
        limit = int(opts["limit"] or 200)
        since_days = opts.get("since_days")
        dry_run = bool(opts.get("dry_run"))
        run_id_prefix = (opts.get("run_id_prefix") or "BACKFILL_LEDGER").strip()
        reason = (opts.get("reason") or "backfill_missing_ledger").strip()
        only_status = opts.get("only_status")
        exclude_status = opts.get("exclude_status")
        job_ids_raw = opts.get("job_ids")

        qs = Job.objects.filter(ledger_entry__isnull=True)

        if job_ids_raw:
            ids = [int(x.strip()) for x in job_ids_raw.split(",") if x.strip()]
            qs = qs.filter(job_id__in=ids)

        if since_days is not None:
            cutoff = timezone.now() - timedelta(days=int(since_days))
            qs = qs.filter(Q(updated_at__gte=cutoff) | Q(created_at__gte=cutoff))

        if only_status:
            qs = qs.filter(job_status=only_status)
        if exclude_status:
            qs = qs.exclude(job_status=exclude_status)

        qs = qs.order_by("job_id")[:limit]

        stats = Stats()
        now_tag = timezone.now().strftime("%Y%m%d_%H%M%S")

        self.stdout.write(
            f"LEDGER_BACKFILL | dry_run={dry_run} | limit={limit} | reason={reason} | run_id_prefix={run_id_prefix}"
        )

        for j in qs:
            stats.scanned += 1
            run_id = f"{run_id_prefix}_{now_tag}_job_{j.job_id}"

            if dry_run:
                self.stdout.write(
                    f"DRY RUN: would rebuild job_id={j.job_id} status={getattr(j, 'job_status', None)} run_id={run_id}"
                )
                stats.skipped += 1
                continue

            try:
                stats.processed += 1
                le = rebuild_platform_ledger_for_job(j.job_id, run_id=run_id, reason=reason)
                stats.rebuilt += 1
                self.stdout.write(
                    f"OK rebuilt job_id={j.job_id} is_final={le.is_final} rebuild_count={le.rebuild_count}"
                )
            except Exception as e:
                stats.errors += 1
                self.stderr.write(f"ERROR job_id={j.job_id}: {type(e).__name__}: {e}")

        self.stdout.write(
            f"SUMMARY | scanned={stats.scanned} processed={stats.processed} rebuilt={stats.rebuilt} "
            f"skipped={stats.skipped} errors={stats.errors}"
        )
