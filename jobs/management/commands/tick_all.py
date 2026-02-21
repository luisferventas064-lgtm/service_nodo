from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Run all ticks: on_demand + marketplace (safe orchestrator)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-on-demand",
            action="store_true",
            help="Skip tick_on_demand",
        )
        parser.add_argument(
            "--skip-marketplace",
            action="store_true",
            help="Skip tick_marketplace",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        self.stdout.write(f"NOW: {now.isoformat()}")
        self.stdout.write("TICK_ALL: start")

        errors = 0

        if not options["skip_on_demand"]:
            try:
                self.stdout.write("TICK_ALL: running tick_on_demand...")
                call_command("tick_on_demand", stdout=self.stdout, stderr=self.stderr)
                self.stdout.write("TICK_ALL: tick_on_demand OK")
            except Exception as exc:
                errors += 1
                self.stderr.write(f"TICK_ALL: tick_on_demand FAILED: {exc!r}")

        if not options["skip_marketplace"]:
            try:
                self.stdout.write("TICK_ALL: running tick_marketplace...")
                call_command("tick_marketplace", stdout=self.stdout, stderr=self.stderr)
                self.stdout.write("TICK_ALL: tick_marketplace OK")
            except Exception as exc:
                errors += 1
                self.stderr.write(f"TICK_ALL: tick_marketplace FAILED: {exc!r}")

        if errors:
            raise SystemExit(f"TICK_ALL: finished with {errors} error(s)")

        self.stdout.write("TICK_ALL: finished OK")
