from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from jobs.ledger import finalize_platform_ledger_for_job


class Command(BaseCommand):
    help = "Finalize (freeze) platform ledger for a job. Idempotent: if already final, does nothing."

    def add_arguments(self, parser):
        parser.add_argument("--job-id", type=int, required=True, help="Job ID")
        parser.add_argument("--run-id", type=str, default=None, help="Run ID (optional)")

    def handle(self, *args, **options):
        job_id = options["job_id"]
        run_id = options.get("run_id")

        try:
            entry = finalize_platform_ledger_for_job(job_id, run_id=run_id)
        except Exception as e:
            raise CommandError(str(e))

        self.stdout.write(
            self.style.SUCCESS(
                "OK finalized "
                f"job_id={entry.job_id} "
                f"is_final={entry.is_final} "
                f"finalized_at={entry.finalized_at.isoformat() if entry.finalized_at else None} "
                f"finalized_run_id={entry.finalized_run_id} "
                f"gross={entry.gross_cents} tax={entry.tax_cents} fee={entry.fee_cents} "
                f"net_provider={entry.net_provider_cents} platform_rev={entry.platform_revenue_cents}"
            )
        )
