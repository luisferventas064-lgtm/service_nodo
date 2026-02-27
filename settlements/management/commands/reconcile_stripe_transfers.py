from django.core.management.base import BaseCommand
from django.conf import settings

from core.stripe_client import get_stripe
from settlements.models import SettlementPayment


class Command(BaseCommand):
    help = "Reconcile Stripe transfers with local SettlementPayment records (SAFE MODE)"

    def _has_field(self, name: str) -> bool:
        return any(f.name == name for f in SettlementPayment._meta.get_fields())

    def handle(self, *args, **options):
        required_fields = {"stripe_transfer_id", "stripe_status", "stripe_environment"}
        missing_fields = [f for f in required_fields if not self._has_field(f)]
        if missing_fields:
            self.stdout.write(
                self.style.ERROR(
                    "SettlementPayment is missing required fields for reconciliation: "
                    f"{', '.join(sorted(missing_fields))}"
                )
            )
            return

        stripe = get_stripe()

        self.stdout.write("Fetching transfers from Stripe...")

        stripe_transfers = {}
        has_more = True
        starting_after = None

        while has_more:
            params = {"limit": 100}
            if starting_after:
                params["starting_after"] = starting_after

            response = stripe.Transfer.list(**params)

            for transfer in response.data:
                stripe_transfers[transfer.id] = transfer

            has_more = bool(response.has_more)
            if has_more and response.data:
                starting_after = response.data[-1].id

        self.stdout.write(f"Stripe transfers fetched: {len(stripe_transfers)}")

        local_payments = SettlementPayment.objects.filter(
            stripe_environment=settings.STRIPE_MODE
        ).exclude(
            stripe_transfer_id__isnull=True
        ).exclude(stripe_transfer_id="")

        self.stdout.write(f"Local payments found: {local_payments.count()}")

        inconsistencies = 0

        for payment in local_payments:
            transfer = stripe_transfers.get(payment.stripe_transfer_id)

            if not transfer:
                inconsistencies += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"[MISSING IN STRIPE] Transfer {payment.stripe_transfer_id}"
                    )
                )
                continue

            if transfer.amount != payment.amount_cents:
                inconsistencies += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"[AMOUNT MISMATCH] {transfer.id} "
                        f"Stripe={transfer.amount} Local={payment.amount_cents}"
                    )
                )

            stripe_status = "success" if transfer.paid else "failed"
            if stripe_status != payment.stripe_status:
                inconsistencies += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"[STATUS MISMATCH] {transfer.id} "
                        f"Stripe={stripe_status} Local={payment.stripe_status}"
                    )
                )

        local_transfer_ids = set(
            local_payments.values_list("stripe_transfer_id", flat=True)
        )

        for transfer_id in stripe_transfers.keys():
            if transfer_id not in local_transfer_ids:
                inconsistencies += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"[MISSING IN DB] Transfer {transfer_id}"
                    )
                )

        if inconsistencies == 0:
            self.stdout.write(self.style.SUCCESS("No inconsistencies detected."))
        else:
            self.stdout.write(
                self.style.ERROR(f"Inconsistencies found: {inconsistencies}")
            )
