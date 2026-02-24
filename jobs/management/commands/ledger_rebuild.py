from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from jobs.ledger import rebuild_platform_ledger_for_job


class Command(BaseCommand):
    help = "Rebuild (force recompute) platform ledger for a job with audit fields, even if finalized."

    def add_arguments(self, parser):
        parser.add_argument("--job-id", type=int, required=True, help="Job ID")
        parser.add_argument("--run-id", type=str, default=None, help="Run ID (optional)")
        parser.add_argument("--reason", type=str, default=None, help="Reason (optional, max 255 chars)")

    def handle(self, *args, **options):
        job_id = options["job_id"]
        run_id = options.get("run_id")
        reason = options.get("reason")

        try:
            entry = rebuild_platform_ledger_for_job(
                job_id,
                run_id=run_id,
                reason=reason,
            )
        except Exception as e:
            raise CommandError(str(e))

        self.stdout.write(
            self.style.SUCCESS(
                "OK rebuilt "
                f"job_id={entry.job_id} "
                f"rebuild_count={entry.rebuild_count} "
                f"is_final={entry.is_final} "
                f"gross={entry.gross_cents} tax={entry.tax_cents} fee={entry.fee_cents} "
                f"net_provider={entry.net_provider_cents} platform_rev={entry.platform_revenue_cents}"
            )
        )
