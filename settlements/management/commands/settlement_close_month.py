from calendar import monthrange
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from settlements.models import MonthlySettlementClose
from settlements.services import generate_settlements_for_period

User = get_user_model()


class Command(BaseCommand):
    help = "Close financial month (global). Generates settlements and locks period."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--month", type=int, required=True)
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--notes", type=str, default="")

    def handle(self, *args, **options):
        year = options["year"]
        month = options["month"]
        user_id = options["user_id"]
        notes = options["notes"]

        last_day = monthrange(year, month)[1]

        start_dt = timezone.make_aware(
            datetime(year, month, 1, 0, 0, 0),
            dt_timezone.utc,
        )

        end_dt = timezone.make_aware(
            datetime(year, month, last_day, 23, 59, 59),
            dt_timezone.utc,
        )

        # Validate period is not already closed
        already_closed = MonthlySettlementClose.objects.filter(
            provider__isnull=True,
            is_global=True,
            period_start=start_dt,
            period_end=end_dt,
        ).exists()

        if already_closed:
            self.stdout.write(self.style.ERROR("Month already closed."))
            return

        user = User.objects.get(id=user_id)

        with transaction.atomic():
            summary = generate_settlements_for_period(
                start_dt.date(),
                end_dt.date(),
            )

            MonthlySettlementClose.objects.create(
                provider=None,
                is_global=True,
                period_start=start_dt,
                period_end=end_dt,
                total_gross_cents=summary["total_gross_cents"],
                total_provider_cents=summary["total_net_provider_cents"],
                total_platform_revenue_cents=summary["total_platform_revenue_cents"],
                closed_by=user,
                notes=notes,
            )

        self.stdout.write(self.style.SUCCESS("Month closed successfully."))
        self.stdout.write(str(summary))
