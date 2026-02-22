from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from jobs.dashboard import dashboard
from jobs.models import KpiSnapshot


class Command(BaseCommand):
    help = "Compute KPI dashboard and save snapshot to DB."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=168)

    def handle(self, *args, **options):
        hours = int(options["hours"])
        data = dashboard(since_hours=hours)

        # Campos de volumen para thresholds dinámicos en automatizaciones externas.
        funnel = data.get("funnel_counts") or {}
        total_jobs = int(funnel.get("posted") or 0)
        timeouts_count = int(funnel.get("timeout") or 0)
        cancels_count = int(funnel.get("cancelled") or 0)
        data["total_jobs"] = total_jobs
        data["timeouts_count"] = timeouts_count
        data["cancels_count"] = cancels_count

        payload = json.dumps(data, default=str, ensure_ascii=False)
        snap = KpiSnapshot.objects.create(window_hours=hours, payload_json=payload)

        self.stdout.write(
            self.style.SUCCESS(
                f"OK saved KpiSnapshot id={snap.id} window_hours={hours}"
            )
        )
