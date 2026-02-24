from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from jobs.evidence import write_job_evidence_json


class Command(BaseCommand):
    help = "Generate a ledger evidence JSON file for a job (manual evidence pack)."

    def add_arguments(self, parser):
        parser.add_argument("--job-id", type=int, required=True, help="Job ID")
        parser.add_argument("--out-dir", type=str, default=None, help="Output directory (optional)")
        parser.add_argument("--run-id", type=str, default=None, help="Run ID (optional)")
        parser.add_argument(
            "--source",
            type=str,
            default="manual",
            choices=["manual", "finalize", "rebuild"],
            help="Evidence source label",
        )

    def handle(self, *args, **options):
        job_id = options["job_id"]
        out_dir = options.get("out_dir")
        run_id = options.get("run_id")
        source = options.get("source") or "manual"

        try:
            path = write_job_evidence_json(
                job_id,
                out_dir=out_dir,
                run_id=run_id,
                source=source,
            )
        except Exception as e:
            raise CommandError(str(e))

        self.stdout.write(self.style.SUCCESS(f"OK evidence_json path={path}"))
