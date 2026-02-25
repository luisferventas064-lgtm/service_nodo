from django.core.management.base import BaseCommand
from django.db.models import Count, Sum

from jobs.models import PlatformLedgerEntry
from providers.models import Provider


class Command(BaseCommand):
    help = "Show pending balance for provider (not yet settled)"

    def add_arguments(self, parser):
        parser.add_argument("--provider_id", type=int, required=True)

    def handle(self, *args, **options):
        provider_id = options["provider_id"]

        try:
            provider = Provider.objects.get(pk=provider_id)

            # PlatformLedgerEntry has no direct provider FK in this project.
            ledger_qs = PlatformLedgerEntry.objects.filter(
                job__selected_provider=provider,
                is_final=True,
                settlement__isnull=True,
            )

            if not ledger_qs.exists():
                self.stdout.write(
                    self.style.WARNING("No pending finalized ledger entries.")
                )
                return

            aggregates = ledger_qs.aggregate(
                total_gross=Sum("gross_cents"),
                total_tax=Sum("tax_cents"),
                total_fee=Sum("fee_cents"),
                total_net_provider=Sum("net_provider_cents"),
                total_platform_revenue=Sum("platform_revenue_cents"),
                total_jobs=Count("id"),
            )

            self.stdout.write(self.style.SUCCESS("=== PENDING BALANCE ==="))
            self.stdout.write(f"Provider ID: {provider_id}")
            self.stdout.write(f"Jobs: {aggregates['total_jobs']}")
            self.stdout.write(f"Gross: {aggregates['total_gross'] or 0}")
            self.stdout.write(f"Tax: {aggregates['total_tax'] or 0}")
            self.stdout.write(f"Fee: {aggregates['total_fee'] or 0}")
            self.stdout.write(f"Net to Provider: {aggregates['total_net_provider'] or 0}")
            self.stdout.write(f"Platform Revenue: {aggregates['total_platform_revenue'] or 0}")

        except Provider.DoesNotExist:
            self.stderr.write("Provider not found")
