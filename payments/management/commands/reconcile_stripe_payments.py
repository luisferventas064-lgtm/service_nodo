from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.management.base import BaseCommand

from core.stripe_client import get_stripe
from payments.models import ClientPayment


@dataclass
class StripeRow:
    pi_id: str
    stripe_status: str
    stripe_amount: int
    stripe_currency: str
    db_status: str | None
    db_amount: int | None
    db_currency: str | None
    job_id: int | None


class Command(BaseCommand):
    help = "Reconcile Stripe PaymentIntents against ClientPayment records (read-only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from-ts",
            required=True,
            help="ISO timestamp UTC, e.g. 2026-02-26T00:00:00Z",
        )
        parser.add_argument(
            "--to-ts",
            required=True,
            help="ISO timestamp UTC, e.g. 2026-02-28T00:00:00Z",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Max Stripe PaymentIntents to fetch",
        )

    def handle(self, *args, **opts):
        from_ts = self._parse_iso_utc(opts["from_ts"])
        to_ts = self._parse_iso_utc(opts["to_ts"])
        limit = int(opts["limit"])

        stripe = get_stripe()
        stripe_rows, truncated = self._fetch_payment_intents(
            stripe=stripe,
            from_ts=from_ts,
            to_ts=to_ts,
            limit=limit,
        )

        missing_in_db = 0
        missing_in_stripe = 0
        mismatches = 0
        ok = 0

        detail_lines: list[str] = []
        stripe_pi_ids = {row.pi_id for row in stripe_rows}

        for row in stripe_rows:
            cp = (
                ClientPayment.objects.select_related("job")
                .filter(stripe_payment_intent_id=row.pi_id)
                .first()
            )
            if not cp:
                missing_in_db += 1
                detail_lines.append(
                    f"- MISSING_IN_DB pi={row.pi_id} stripe=({row.stripe_status},{row.stripe_amount},{row.stripe_currency})"
                )
                continue

            row.db_status = cp.stripe_status
            row.db_amount = cp.amount_cents
            row.db_currency = self._db_currency(cp)
            row.job_id = cp.job_id

            status_match = row.db_status == row.stripe_status
            amount_match = row.db_amount == row.stripe_amount
            currency_match = self._normalize_currency(row.db_currency) == row.stripe_currency
            if status_match and amount_match and currency_match:
                ok += 1
                continue

            mismatches += 1
            detail_lines.append(
                f"- MISMATCH pi={row.pi_id} job_id={row.job_id} "
                f"stripe=({row.stripe_status},{row.stripe_amount},{row.stripe_currency}) "
                f"db=({row.db_status},{row.db_amount},{row.db_currency})"
            )

        local_qs = (
            ClientPayment.objects.filter(created_at__gte=from_ts, created_at__lte=to_ts)
            .exclude(stripe_payment_intent_id__isnull=True)
            .exclude(stripe_payment_intent_id="")
        )
        for pi_id, job_id in local_qs.values_list("stripe_payment_intent_id", "job_id"):
            if pi_id not in stripe_pi_ids:
                remote_pi = self._safe_retrieve_payment_intent(stripe=stripe, pi_id=pi_id)
                if remote_pi is not None:
                    metadata = remote_pi.get("metadata") or {}
                    if not self._include_payment_intent_for_reconciliation(metadata):
                        continue
                missing_in_stripe += 1
                detail_lines.append(f"- MISSING_IN_STRIPE pi={pi_id} job_id={job_id}")

        total = len(stripe_rows)
        self.stdout.write(self.style.SUCCESS(f"Stripe PIs scanned: {total}"))
        self.stdout.write(
            f"OK: {ok} | mismatches: {mismatches} | missing_in_db: {missing_in_db} | missing_in_stripe: {missing_in_stripe}"
        )
        if truncated:
            self.stdout.write(
                self.style.WARNING(
                    "Result truncated by --limit. Increase --limit for a full reconciliation window."
                )
            )

        if detail_lines:
            self.stdout.write("\nDETAILS (first 50):")
            for line in detail_lines[:50]:
                self.stdout.write(line)

    def _fetch_payment_intents(self, *, stripe, from_ts: datetime, to_ts: datetime, limit: int):
        rows: list[StripeRow] = []
        created = {"gte": int(from_ts.timestamp()), "lte": int(to_ts.timestamp())}
        starting_after = None
        truncated = False

        while len(rows) < limit:
            page_limit = min(100, limit - len(rows))
            params = {
                "created": created,
                "limit": page_limit,
            }
            if starting_after:
                params["starting_after"] = starting_after

            page = stripe.PaymentIntent.list(**params)
            if not page.data:
                break

            for pi in page.data:
                metadata = pi.get("metadata") or {}
                if not self._include_payment_intent_for_reconciliation(metadata):
                    continue

                rows.append(
                    StripeRow(
                        pi_id=pi["id"],
                        stripe_status=self._normalize_stripe_status(pi),
                        stripe_amount=int(pi.get("amount") or 0),
                        stripe_currency=self._normalize_currency(pi.get("currency")),
                        db_status=None,
                        db_amount=None,
                        db_currency=None,
                        job_id=None,
                    )
                )
                if len(rows) >= limit:
                    break

            if len(rows) >= limit:
                truncated = bool(page.has_more)
                break

            if not page.has_more:
                break

            starting_after = page.data[-1].id

        return rows, truncated

    @staticmethod
    def _parse_iso_utc(value: str) -> datetime:
        normalized = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_timezone.utc)
        return dt.astimezone(dt_timezone.utc)

    @staticmethod
    def _normalize_currency(value) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _include_payment_intent_for_reconciliation(metadata: dict) -> bool:
        if str(metadata.get("nodo") or "").strip() != "1":
            return False

        nodo_env = str(metadata.get("nodo_env") or "").strip().lower()
        current_env = str(settings.STRIPE_MODE).strip().lower()
        if nodo_env and nodo_env != current_env:
            return False
        return True

    @staticmethod
    def _normalize_stripe_status(pi) -> str:
        status = str(pi.get("status") or "").strip().lower()

        if status == "succeeded":
            return "succeeded"

        if status in {
            "requires_payment_method",
            "requires_confirmation",
            "requires_action",
            "processing",
        }:
            if pi.get("last_payment_error"):
                return "failed"
            return "created"

        return status

    def _db_currency(self, payment: ClientPayment) -> str | None:
        if hasattr(payment, "currency") and payment.currency:
            return str(payment.currency)

        job = getattr(payment, "job", None)
        if job is not None and getattr(job, "quoted_currency_code", None):
            return str(job.quoted_currency_code)

        return None

    @staticmethod
    def _safe_retrieve_payment_intent(*, stripe, pi_id: str):
        try:
            return stripe.PaymentIntent.retrieve(pi_id)
        except Exception:
            return None
