from django.core.management.base import BaseCommand

from settlements.models import ProviderSettlement


class Command(BaseCommand):
    help = "Show detailed ledger entries for a settlement"

    def add_arguments(self, parser):
        parser.add_argument("--settlement_id", type=int, required=True)

    def handle(self, *args, **options):
        settlement_id = options["settlement_id"]

        try:
            settlement = ProviderSettlement.objects.select_related("provider").get(
                pk=settlement_id
            )

            self.stdout.write(self.style.SUCCESS("=== SETTLEMENT DETAIL ==="))
            self.stdout.write(f"Settlement ID: {settlement.id}")
            self.stdout.write(f"Provider ID: {settlement.provider_id}")
            self.stdout.write(f"Period: {settlement.period_start} → {settlement.period_end}")
            self.stdout.write(f"Status: {settlement.status}")
            self.stdout.write("")

            self.stdout.write("---- SNAPSHOT TOTALS ----")
            self.stdout.write(f"Gross: {settlement.total_gross_cents}")
            self.stdout.write(f"Tax: {settlement.total_tax_cents}")
            self.stdout.write(f"Fee: {settlement.total_fee_cents}")
            self.stdout.write(f"Net Provider: {settlement.total_net_provider_cents}")
            self.stdout.write(f"Platform Revenue: {settlement.total_platform_revenue_cents}")
            self.stdout.write(f"Jobs Count: {settlement.total_jobs}")
            self.stdout.write("")

            self.stdout.write("---- LEDGER ENTRIES ----")

            ledger_entries = settlement.ledger_entries.all().order_by("finalized_at")

            if not ledger_entries.exists():
                self.stdout.write("No ledger entries linked.")
                return

            for entry in ledger_entries:
                self.stdout.write(
                    f"LedgerID={entry.id} "
                    f"JobID={entry.job_id} "
                    f"Gross={entry.gross_cents} "
                    f"Net={entry.net_provider_cents} "
                    f"Fee={entry.fee_cents} "
                    f"Tax={entry.tax_cents} "
                    f"Finalized={entry.finalized_at}"
                )

        except ProviderSettlement.DoesNotExist:
            self.stderr.write("Settlement not found")
