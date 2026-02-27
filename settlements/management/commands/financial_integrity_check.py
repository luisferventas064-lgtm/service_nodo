from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q, Sum

from jobs.models import PlatformLedgerEntry
from payments.models import ClientCreditNote, ClientPayment
from settlements.models import ProviderSettlement, SettlementStatus


class Command(BaseCommand):
    help = (
        "Run global financial integrity checks: "
        "PAID settlement immutability, refund limits, and ledger/settlement gross parity."
    )

    def handle(self, *args, **options):
        errors: list[str] = []

        paid_link_mutations = PlatformLedgerEntry.objects.filter(
            settlement__status=SettlementStatus.PAID,
            settlement__paid_at__isnull=False,
        ).filter(
            Q(created_at__gt=F("settlement__paid_at"))
            | Q(updated_at__gt=F("settlement__paid_at"))
        )
        mutation_count = paid_link_mutations.count()
        if mutation_count:
            sample_ids = list(paid_link_mutations.values_list("id", flat=True)[:10])
            errors.append(
                "PAID settlement mutation detected "
                f"(ledger_count={mutation_count}, sample_ledger_ids={sample_ids})."
            )

        paid_rows = (
            ClientPayment.objects.filter(stripe_status__in=["succeeded", "success", "paid"])
            .values("job_id", "stripe_environment")
            .annotate(total=Sum("amount_cents"))
        )
        paid_map = {
            (int(r["job_id"]), str(r["stripe_environment"])): int(r["total"] or 0)
            for r in paid_rows
        }
        refund_rows = (
            ClientCreditNote.objects.values("client_payment__job_id", "stripe_environment")
            .annotate(total=Sum("amount_cents"))
        )
        refund_exceeded: list[tuple[int, str, int, int]] = []
        for row in refund_rows:
            job_id = int(row["client_payment__job_id"])
            env = str(row["stripe_environment"])
            refunded = int(row["total"] or 0)
            paid = int(paid_map.get((job_id, env), 0))
            if refunded > paid:
                refund_exceeded.append((job_id, env, refunded, paid))

        if refund_exceeded:
            sample = refund_exceeded[:10]
            errors.append(
                "Refund total exceeds paid total "
                f"(violations={len(refund_exceeded)}, sample={sample})."
            )

        ledger_gross = int(
            PlatformLedgerEntry.objects.filter(settlement__isnull=False).aggregate(
                total=Sum("gross_cents")
            )["total"]
            or 0
        )
        settlement_gross = int(
            ProviderSettlement.objects.exclude(status=SettlementStatus.CANCELLED).aggregate(
                total=Sum("total_gross_cents")
            )["total"]
            or 0
        )
        if ledger_gross != settlement_gross:
            errors.append(
                "Ledger/Settlement gross mismatch "
                f"(ledger_gross={ledger_gross}, settlement_gross={settlement_gross})."
            )

        if errors:
            for msg in errors:
                self.stderr.write(self.style.ERROR(f"FAIL: {msg}"))
            raise CommandError(f"financial_integrity_check failed ({len(errors)} issue(s)).")

        self.stdout.write(
            self.style.SUCCESS(
                "OK financial_integrity_check: "
                f"paid_link_mutations=0 refund_exceeded=0 ledger_gross={ledger_gross}"
            )
        )
