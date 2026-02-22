from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from jobs.dashboard import dashboard


class Command(BaseCommand):
    help = "Print KPI snapshot (dashboard) as JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=168,
            help="Lookback window in hours (default: 168).",
        )

    def handle(self, *args, **options):
        hours = int(options["hours"])
        data = dashboard(since_hours=hours)

        # Compact and stable JSON output for logs/cron
        self.stdout.write(json.dumps(data, default=str, ensure_ascii=False, indent=2))
