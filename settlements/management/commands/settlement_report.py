from django.core.management.base import BaseCommand
from django.db.models import Sum

from settlements.models import ProviderSettlement, SettlementStatus


class Command(BaseCommand):
    help = "Global settlement financial report"

    def handle(self, *args, **options):
        settlements = ProviderSettlement.objects.all().order_by("-period_start")

        if not settlements.exists():
            self.stdout.write("No settlements found.")
            return

        self.stdout.write(self.style.SUCCESS("=== SETTLEMENT REPORT ==="))
        self.stdout.write("")

        for s in settlements:
            self.stdout.write(
                f"ID={s.id} "
                f"Provider={s.provider_id} "
                f"Period={s.period_start.date()}→{s.period_end.date()} "
                f"Status={s.status} "
                f"Net={s.total_net_provider_cents} "
                f"PlatformRev={s.total_platform_revenue_cents} "
                f"Jobs={s.total_jobs} "
                f"Created={s.created_at}"
            )

        self.stdout.write("")
        self.stdout.write("---- CONSOLIDATED TOTALS ----")

        totals = settlements.aggregate(
            total_net=Sum("total_net_provider_cents"),
            total_platform=Sum("total_platform_revenue_cents"),
        )

        draft_total = settlements.filter(
            status=SettlementStatus.DRAFT
        ).aggregate(total=Sum("total_net_provider_cents"))["total"] or 0

        closed_total = settlements.filter(
            status=SettlementStatus.CLOSED
        ).aggregate(total=Sum("total_net_provider_cents"))["total"] or 0

        paid_total = settlements.filter(
            status=SettlementStatus.PAID
        ).aggregate(total=Sum("total_net_provider_cents"))["total"] or 0

        self.stdout.write(f"Total Net (All): {totals['total_net'] or 0}")
        self.stdout.write(f"Total Platform Revenue: {totals['total_platform'] or 0}")
        self.stdout.write(f"Draft: {draft_total}")
        self.stdout.write(f"Closed (not paid yet): {closed_total}")
        self.stdout.write(f"Paid: {paid_total}")
