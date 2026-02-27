from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from clients.models import ClientTicket
from core.stripe_client import get_stripe
from jobs.models import Job, PlatformLedgerEntry
from payments.models import ClientCreditNote, ClientPayment


def _get_final_client_ticket_for_job(job: Job) -> ClientTicket:
    final_ticket = (
        ClientTicket.objects.select_for_update()
        .filter(
            ref_type="job",
            ref_id=job.pk,
            stage=ClientTicket.Stage.FINAL,
            status=ClientTicket.Status.FINALIZED,
        )
        .only("total_cents")
        .first()
    )
    if not final_ticket:
        raise ValidationError("No final client ticket available for payment")
    return final_ticket


def create_payment_intent_for_job(job: Job) -> str:
    stripe = get_stripe()
    idempotency_key = f"job_{job.pk}_payment_intent_{settings.STRIPE_MODE}"

    with transaction.atomic():
        locked_job = Job.objects.select_for_update().get(pk=job.pk)
        final_ticket = _get_final_client_ticket_for_job(locked_job)
        amount = int(final_ticket.total_cents or 0)
        if amount <= 0:
            raise ValidationError("Final client ticket total must be greater than zero")

        payment = ClientPayment.objects.create(
            job_id=job.pk,
            stripe_payment_intent_id=f"pending_{job.pk}_{uuid4().hex}",
            amount_cents=amount,
            stripe_status="created",
            stripe_environment=settings.STRIPE_MODE,
        )

        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency="cad",
            metadata={
                "job_id": job.pk,
                "nodo": "1",
                "nodo_env": str(settings.STRIPE_MODE),
                "nodo_job_id": str(job.pk),
                "nodo_client_payment_id": str(payment.pk),
            },
            idempotency_key=idempotency_key,
        )

        duplicate = (
            ClientPayment.objects.select_for_update()
            .filter(stripe_payment_intent_id=intent.id)
            .exclude(pk=payment.pk)
            .first()
        )
        if duplicate:
            payment.delete()
            return intent.client_secret

        payment.stripe_payment_intent_id = intent.id
        payment.save(update_fields=["stripe_payment_intent_id", "updated_at"])

    return intent.client_secret


def _resolve_payment_for_refunded_charge(
    *,
    charge_id: str | None,
    payment_intent_id: str | None,
) -> ClientPayment:
    payment_qs = (
        ClientPayment.objects.select_for_update()
        .select_related("job")
        .filter(stripe_environment=settings.STRIPE_MODE)
    )

    payment = None
    if charge_id:
        payment = payment_qs.filter(stripe_charge_id=charge_id).first()
    if payment is None and payment_intent_id:
        payment = payment_qs.filter(stripe_payment_intent_id=payment_intent_id).first()

    if payment is None:
        raise ValidationError("ClientPayment not found for refunded charge")

    if charge_id and payment.stripe_charge_id != charge_id:
        payment.stripe_charge_id = charge_id
        payment.save(update_fields=["stripe_charge_id", "updated_at"])

    return payment


def _get_final_ticket_for_payment(payment: ClientPayment) -> ClientTicket:
    final_ticket = (
        ClientTicket.objects.select_for_update()
        .filter(
            ref_type="job",
            ref_id=payment.job_id,
            stage=ClientTicket.Stage.FINAL,
            status=ClientTicket.Status.FINALIZED,
        )
        .first()
    )
    if not final_ticket:
        raise ValidationError("No finalized client ticket for refunded payment")
    return final_ticket


def _validate_refund_not_exceeding_paid_amount(
    *,
    ticket: ClientTicket,
    payment: ClientPayment,
    refund_amount_cents: int,
) -> None:
    refunded_total = int(
        ClientCreditNote.objects.select_for_update()
        .filter(
            ticket=ticket,
            stripe_environment=settings.STRIPE_MODE,
        )
        .aggregate(total=Sum("amount_cents"))["total"]
        or 0
    )
    paid_total = int(
        ClientPayment.objects.select_for_update()
        .filter(
            job_id=payment.job_id,
            stripe_environment=settings.STRIPE_MODE,
            stripe_status__in=["succeeded", "success", "paid"],
        )
        .aggregate(total=Sum("amount_cents"))["total"]
        or 0
    )
    if paid_total <= 0:
        paid_total = int(payment.amount_cents or 0)

    limit = paid_total
    if refunded_total + refund_amount_cents > limit:
        raise ValidationError("Refund exceeds total paid amount")


def _prorated_component_cents(*, component_cents: int, ratio: Decimal) -> int:
    if component_cents == 0:
        return 0
    proportional = Decimal(component_cents) * ratio
    return int(proportional.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _create_refund_compensating_ledger_entry(
    *,
    payment: ClientPayment,
    refund_amount_cents: int,
    credit_note: ClientCreditNote,
) -> None:
    base_ledger_entry = (
        PlatformLedgerEntry.objects.select_for_update()
        .filter(
            job_id=payment.job_id,
            is_adjustment=False,
            is_final=True,
        )
        .first()
    )
    if not base_ledger_entry:
        raise ValidationError("Final ledger entry not found for refunded payment")

    total_gross_cents = int(base_ledger_entry.gross_cents or 0)
    if total_gross_cents <= 0:
        raise ValidationError("Base ledger gross must be greater than zero")

    ratio = Decimal(refund_amount_cents) / Decimal(total_gross_cents)

    provider_component = _prorated_component_cents(
        component_cents=int(base_ledger_entry.net_provider_cents or 0),
        ratio=ratio,
    )
    platform_component = _prorated_component_cents(
        component_cents=int(base_ledger_entry.platform_revenue_cents or 0),
        ratio=ratio,
    )
    tax_component = _prorated_component_cents(
        component_cents=int(base_ledger_entry.tax_cents or 0),
        ratio=ratio,
    )
    calculated_total = provider_component + platform_component + tax_component
    residual = refund_amount_cents - calculated_total
    # FINANCIAL INVARIANT - DO NOT MODIFY:
    # Residual cents must be absorbed by platform to keep provider/tax stable.
    # Platform fee/revenue is the accounting buffer for rounding residuals.
    platform_component += residual

    if provider_component + platform_component + tax_component != refund_amount_cents:
        raise ValueError("Refund distribution mismatch")

    fee_component = platform_component

    PlatformLedgerEntry.objects.create(
        job_id=payment.job_id,
        settlement=None,
        currency=(base_ledger_entry.currency or "CAD"),
        gross_cents=-refund_amount_cents,
        tax_cents=-tax_component,
        fee_cents=-fee_component,
        net_provider_cents=-provider_component,
        platform_revenue_cents=-platform_component,
        fee_payer=base_ledger_entry.fee_payer,
        tax_region_code=base_ledger_entry.tax_region_code,
        is_adjustment=True,
        is_final=True,
        finalized_at=timezone.now(),
        finalized_run_id=f"CREDIT_NOTE_{credit_note.stripe_refund_id}",
        finalize_version=1,
    )


@transaction.atomic
def create_credit_note_from_stripe_refund(charge: dict, refund: dict) -> ClientCreditNote:
    refund_id = refund.get("id")
    if not refund_id:
        raise ValidationError("Refund id is required")

    refund_amount_cents = int(refund.get("amount") or 0)
    if refund_amount_cents <= 0:
        raise ValidationError("Refund amount must be greater than zero")

    existing = (
        ClientCreditNote.objects.select_for_update()
        .filter(stripe_refund_id=refund_id)
        .first()
    )
    if existing:
        return existing

    charge_id = charge.get("id")
    payment_intent_id = charge.get("payment_intent")
    payment = _resolve_payment_for_refunded_charge(
        charge_id=charge_id,
        payment_intent_id=payment_intent_id,
    )
    ticket = _get_final_ticket_for_payment(payment)

    _validate_refund_not_exceeding_paid_amount(
        ticket=ticket,
        payment=payment,
        refund_amount_cents=refund_amount_cents,
    )

    reason = refund.get("reason") or "stripe_refund"
    currency = str(charge.get("currency") or ticket.currency or "CAD").upper()
    credit_note = ClientCreditNote.objects.create(
        ticket=ticket,
        client_payment=payment,
        amount_cents=refund_amount_cents,
        currency=currency[:10],
        reason=reason,
        stripe_refund_id=refund_id,
        stripe_environment=settings.STRIPE_MODE,
    )

    _create_refund_compensating_ledger_entry(
        payment=payment,
        refund_amount_cents=refund_amount_cents,
        credit_note=credit_note,
    )
    return credit_note


@transaction.atomic
def create_credit_notes_from_charge_refunded_event(charge: dict) -> list[ClientCreditNote]:
    refunds = list((charge.get("refunds") or {}).get("data") or [])
    if not refunds:
        amount_refunded = int(charge.get("amount_refunded") or 0)
        refund_id = charge.get("refund")
        if refund_id and amount_refunded > 0:
            refunds = [{"id": refund_id, "amount": amount_refunded, "reason": "stripe_refund"}]

    created_notes: list[ClientCreditNote] = []
    for refund in refunds:
        note = create_credit_note_from_stripe_refund(charge, refund)
        created_notes.append(note)
    return created_notes
