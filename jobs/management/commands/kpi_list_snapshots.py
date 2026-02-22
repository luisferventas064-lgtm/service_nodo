from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from jobs.models import KpiSnapshot


class Command(BaseCommand):
    help = "List KPI snapshots (latest first)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument(
            "--show",
            action="store_true",
            help="Print payload_json for the latest snapshot",
        )

    def handle(self, *args, **options):
        limit = int(options["limit"])
        show = bool(options["show"])

        qs = KpiSnapshot.objects.order_by("-created_at")[:limit]
        for s in qs:
            self.stdout.write(
                f"id={s.id} created_at={s.created_at} window_hours={s.window_hours} payload_len={len(s.payload_json)}"
            )

        if show:
            latest = KpiSnapshot.objects.order_by("-created_at").first()
            if not latest:
                self.stdout.write("No snapshots found.")
                return
            self.stdout.write("\n--- LATEST PAYLOAD (pretty) ---")
            try:
                self.stdout.write(
                    json.dumps(json.loads(latest.payload_json), ensure_ascii=False, indent=2)
                )
            except Exception:
                # si no es JSON valido por alguna razon, lo imprime raw
                self.stdout.write(latest.payload_json)
