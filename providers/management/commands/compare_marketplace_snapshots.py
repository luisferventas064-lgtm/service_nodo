from django.core.management.base import BaseCommand, CommandError

from providers.models import MarketplaceAnalyticsSnapshot
from providers.services_analytics import compute_snapshot_diff


class Command(BaseCommand):
    help = "Compare two marketplace analytics snapshots."

    def add_arguments(self, parser):
        parser.add_argument(
            "--id1",
            type=int,
            help="Previous snapshot ID to compare.",
        )
        parser.add_argument(
            "--id2",
            type=int,
            help="Current snapshot ID to compare.",
        )

    def handle(self, *args, **options):
        snapshot_id_1 = options.get("id1")
        snapshot_id_2 = options.get("id2")

        if bool(snapshot_id_1) != bool(snapshot_id_2):
            raise CommandError("Use both --id1 and --id2, or neither.")

        if snapshot_id_1 and snapshot_id_2:
            previous_snapshot = MarketplaceAnalyticsSnapshot.objects.filter(
                pk=snapshot_id_1,
            ).first()
            current_snapshot = MarketplaceAnalyticsSnapshot.objects.filter(
                pk=snapshot_id_2,
            ).first()

            if previous_snapshot is None:
                raise CommandError(f"Snapshot {snapshot_id_1} does not exist.")
            if current_snapshot is None:
                raise CommandError(f"Snapshot {snapshot_id_2} does not exist.")
        else:
            latest_two = list(
                MarketplaceAnalyticsSnapshot.objects.order_by("-captured_at")[:2]
            )
            if len(latest_two) < 2:
                raise CommandError(
                    "At least two marketplace snapshots are required for comparison."
                )
            current_snapshot = latest_two[0]
            previous_snapshot = latest_two[1]

        diff = compute_snapshot_diff(current_snapshot, previous_snapshot)

        self.stdout.write(
            f"Snapshot {current_snapshot.marketplace_analytics_snapshot_id} "
            f"vs Snapshot {previous_snapshot.marketplace_analytics_snapshot_id}"
        )
        self.stdout.write("")
        self.stdout.write(
            f"total_providers: {self._format_delta(diff['total_providers_delta'], digits=0)}"
        )
        self.stdout.write(
            f"verified_pct: {self._format_delta(diff['verified_pct_delta'], suffix='%', digits=2)}"
        )
        self.stdout.write(
            f"avg_price: {self._format_delta(diff['avg_price_delta'], digits=2)}"
        )
        self.stdout.write(
            f"avg_hybrid_score: {self._format_delta(diff['avg_hybrid_score_delta'], digits=4)}"
        )
        self.stdout.write(
            f"score_std_dev: {self._format_delta(diff['score_std_dev_delta'], digits=4)}"
        )
        self.stdout.write(
            "max_competitiveness_index: "
            f"{self._format_delta(diff['max_competitiveness_index_delta'], digits=4)}"
        )
        self.stdout.write("")

        drift_signals = self._build_drift_signals(diff)
        if drift_signals:
            self.stdout.write("Drift signals:")
            for signal in drift_signals:
                self.stdout.write(f"- {signal}")
        else:
            self.stdout.write("No material drift detected.")

    def _format_delta(self, value, *, suffix="", digits=4):
        if value is None:
            return "n/a"

        if digits == 0:
            normalized = str(int(value))
        else:
            normalized = f"{value:.{digits}f}"

        if not normalized.startswith("-"):
            normalized = f"+{normalized}"

        return f"{normalized}{suffix}"

    def _build_drift_signals(self, diff):
        signals = []

        if self._abs(diff.get("total_providers_delta")) >= 5:
            signals.append("provider inventory shift detected")

        if self._abs(diff.get("avg_price_delta")) >= 5:
            signals.append("pricing shift detected")

        if self._abs(diff.get("avg_hybrid_score_delta")) >= 0.02:
            signals.append("average hybrid score drift detected")

        if self._abs(diff.get("score_std_dev_delta")) > 0.03:
            signals.append("dispersion shift detected")

        if self._abs(diff.get("max_competitiveness_index_delta")) > 0.05:
            signals.append("compression shift detected")

        return signals

    def _abs(self, value):
        if value is None:
            return 0
        return abs(value)
