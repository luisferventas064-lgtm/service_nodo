import csv
import os
import uuid
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date
from settlements.models import ProviderSettlement, SettlementExportEvidence
from jobs.models import PlatformLedgerEntry


class Command(BaseCommand):
    help = "Export settlements to professional CSV format."

    def add_arguments(self, parser):
        parser.add_argument("--settlement-id", type=int)
        parser.add_argument("--from", dest="date_from", type=str)
        parser.add_argument("--to", dest="date_to", type=str)
        parser.add_argument("--status", type=str)
        parser.add_argument("--provider-id", type=int)
        parser.add_argument("--output", type=str)
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force re-export even if already exported.",
        )

    def handle(self, *args, **options):

        settlement_id = options.get("settlement_id")
        date_from = options.get("date_from")
        date_to = options.get("date_to")
        status = options.get("status")
        provider_id = options.get("provider_id")
        output_path = options.get("output")
        force_export = bool(options.get("force"))

        from_date_value = None
        to_date_value = None

        if settlement_id:
            mode = "single"
            settlements = ProviderSettlement.objects.filter(
                pk=settlement_id
            ).exclude(status="cancelled")
        else:
            mode = "range"
            if not date_from or not date_to:
                raise CommandError("Must provide --from and --to if no --settlement-id is given.")

            from_date_value = parse_date(date_from)
            to_date_value = parse_date(date_to)
            if not from_date_value or not to_date_value:
                raise CommandError("Invalid date format for --from/--to. Use YYYY-MM-DD.")
            if from_date_value > to_date_value:
                raise CommandError("--to must be greater than or equal to --from.")

            settlements = ProviderSettlement.objects.filter(
                period_start__date__gte=from_date_value,
                period_end__date__lte=to_date_value
            ).exclude(status="cancelled")

        if status:
            settlements = settlements.filter(status=status)

        if provider_id:
            settlements = settlements.filter(provider_id=provider_id)

        if not settlements.exists():
            raise CommandError("No settlements found.")

        settlement_instance = settlements.first() if mode == "single" else None
        settlements_count = settlements.count()
        currencies = list(settlements.values_list("currency", flat=True).distinct())
        if len(currencies) > 1:
            raise CommandError("Cannot export settlements with mixed currencies.")
        currency = currencies[0]

        if mode == "single":
            already_exported = SettlementExportEvidence.objects.filter(
                mode="single",
                settlement=settlement_instance,
            ).exists()
            if already_exported and not force_export:
                raise CommandError(
                    "Settlement already exported. Use --force to re-export."
                )
        else:
            already_exported = SettlementExportEvidence.objects.filter(
                mode="range",
                from_date=from_date_value,
                to_date=to_date_value,
            ).exists()
            if already_exported and not force_export:
                raise CommandError(
                    "Range already exported. Use --force to re-export."
                )

        export_timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")

        if not output_path:
            base_dir = os.path.join("reports", "exports", "settlements")
            os.makedirs(base_dir, exist_ok=True)

            if settlement_id:
                filename = f"settlement_{settlement_id}_{export_timestamp}.csv"
            else:
                filename = (
                    f"settlements_{from_date_value.strftime('%Y%m%d')}"
                    f"_{to_date_value.strftime('%Y%m%d')}_{export_timestamp}.csv"
                )

            output_path = os.path.join(base_dir, filename)

        headers = [
            "settlement_id",
            "provider_id",
            "period_start",
            "period_end",
            "settlement_status",
            "ledger_entry_id",
            "job_id",
            "gross_cents",
            "tax_cents",
            "fee_cents",
            "net_provider_cents",
            "platform_revenue_cents",
            "gross_amount",
            "tax_amount",
            "fee_amount",
            "net_provider_amount",
            "platform_revenue_amount",
            "currency",
            "settlement_created_at",
            "settlement_approved_at",
            "settlement_paid_at",
            "exported_at",
        ]

        total_gross = 0
        total_tax = 0
        total_fee = 0
        total_net = 0
        total_platform = 0
        total_rows = 0
        exported_at = timezone.now()

        with open(output_path, mode="w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(headers)

            for settlement in settlements:
                ledger_entries = PlatformLedgerEntry.objects.filter(
                    settlement=settlement
                )

                for entry in ledger_entries:

                    total_gross += entry.gross_cents
                    total_tax += entry.tax_cents
                    total_fee += entry.fee_cents
                    total_net += entry.net_provider_cents
                    total_platform += entry.platform_revenue_cents
                    total_rows += 1

                    writer.writerow([
                        settlement.id,
                        settlement.provider_id,
                        settlement.period_start,
                        settlement.period_end,
                        settlement.status,
                        entry.id,
                        entry.job_id,
                        entry.gross_cents,
                        entry.tax_cents,
                        entry.fee_cents,
                        entry.net_provider_cents,
                        entry.platform_revenue_cents,
                        Decimal(entry.gross_cents) / 100,
                        Decimal(entry.tax_cents) / 100,
                        Decimal(entry.fee_cents) / 100,
                        Decimal(entry.net_provider_cents) / 100,
                        Decimal(entry.platform_revenue_cents) / 100,
                        settlement.currency,
                        settlement.created_at,
                        settlement.approved_at,
                        settlement.paid_at,
                        exported_at,
                    ])

            # Fila resumen
            writer.writerow([])
            writer.writerow([
                "TOTALS",
                "",
                "",
                "",
                "",
                "",
                "",
                total_gross,
                total_tax,
                total_fee,
                total_net,
                total_platform,
                Decimal(total_gross) / 100,
                Decimal(total_tax) / 100,
                Decimal(total_fee) / 100,
                Decimal(total_net) / 100,
                Decimal(total_platform) / 100,
                "",
                "",
                "",
                "",
                exported_at,
            ])

        if not os.path.exists(output_path):
            raise CommandError("Export failed: CSV file was not created.")

        file_size = os.path.getsize(output_path)
        file_name = os.path.basename(output_path)
        run_id = f"settlement_export_{uuid.uuid4().hex}"

        evidence = SettlementExportEvidence(
            mode=mode,
            run_id=run_id,
            settlement=settlement_instance if mode == "single" else None,
            from_date=from_date_value if mode == "range" else None,
            to_date=to_date_value if mode == "range" else None,
            settlements_count=settlements_count,
            total_rows=total_rows,
            total_gross_cents=total_gross,
            total_tax_cents=total_tax,
            total_fee_cents=total_fee,
            total_net_provider_cents=total_net,
            total_platform_revenue_cents=total_platform,
            currency=currency,
            file_path=output_path,
            file_name=file_name,
            file_size_bytes=file_size,
        )
        evidence.full_clean()
        evidence.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Settlement export created at {output_path} (run_id={run_id})"
            )
        )
