import stripe
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt

from jobs.ledger import finalize_platform_ledger_for_job
from jobs.models import PlatformLedgerEntry
from payments.models import ClientPayment, StripeWebhookEvent
from payments.services import create_credit_notes_from_charge_refunded_event
from providers.models import Provider
from settlements.models import SettlementPayment


def _event_payload(event):
    if hasattr(event, "to_dict_recursive"):
        return event.to_dict_recursive()
    return dict(event)


def _ledger_already_final(job_id: int) -> bool:
    return PlatformLedgerEntry.objects.filter(job_id=job_id, is_final=True).exists()


@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except stripe.error.SignatureVerificationError:
        return HttpResponseBadRequest("Invalid signature")
    except Exception:
        return HttpResponseBadRequest("Invalid payload")

    try:
        event_id = event["id"]
        event_type = event["type"]
        account_obj = event.get("data", {}).get("object", {})
        stripe_account_id = account_obj.get("id") if event_type == "account.updated" else None
    except Exception:
        return HttpResponseBadRequest("Invalid payload")

    webhook_event, created = StripeWebhookEvent.objects.get_or_create(
        event_id=event_id,
        defaults={
            "event_type": event_type,
            "payload": _event_payload(event),
            "stripe_account_id": stripe_account_id,
        },
    )
    if not created:
        return HttpResponse(status=200)

    try:
        if event_type == "account.updated":
            account = event["data"]["object"]
            stripe_account_id = account["id"]

            try:
                with transaction.atomic():
                    provider = (
                        Provider.objects.select_for_update()
                        .get(stripe_account_id=stripe_account_id)
                    )

                    provider.stripe_onboarding_completed = account.get(
                        "details_submitted",
                        False,
                    )
                    provider.stripe_payouts_enabled = account.get(
                        "payouts_enabled",
                        False,
                    )
                    provider.stripe_charges_enabled = account.get(
                        "charges_enabled",
                        False,
                    )
                    provider.stripe_account_status = (
                        "active" if account.get("payouts_enabled") else "restricted"
                    )

                    provider.save(
                        update_fields=[
                            "stripe_onboarding_completed",
                            "stripe_payouts_enabled",
                            "stripe_charges_enabled",
                            "stripe_account_status",
                        ]
                    )
            except Provider.DoesNotExist:
                pass
        elif event_type == "transfer.paid":
            transfer = event["data"]["object"]
            transfer_id = transfer["id"]

            try:
                with transaction.atomic():
                    payment = (
                        SettlementPayment.objects.select_for_update()
                        .get(stripe_transfer_id=transfer_id)
                    )
                    payment.stripe_status = "success"
                    payment.stripe_failure_reason = None
                    payment.save(update_fields=["stripe_status", "stripe_failure_reason"])
            except SettlementPayment.DoesNotExist:
                pass
        elif event_type == "transfer.failed":
            transfer = event["data"]["object"]
            transfer_id = transfer["id"]

            try:
                with transaction.atomic():
                    payment = (
                        SettlementPayment.objects.select_for_update()
                        .get(stripe_transfer_id=transfer_id)
                    )
                    payment.stripe_status = "failed"
                    payment.stripe_failure_reason = transfer.get("failure_message")
                    payment.save(update_fields=["stripe_status", "stripe_failure_reason"])
            except SettlementPayment.DoesNotExist:
                pass
        elif event_type == "payment_intent.succeeded":
            intent = event["data"]["object"]
            intent_id = intent["id"]
            latest_charge_id = intent.get("latest_charge")

            try:
                with transaction.atomic():
                    payment = (
                        ClientPayment.objects.select_for_update()
                        .select_related("job")
                        .get(stripe_payment_intent_id=intent_id)
                    )
                    if payment.stripe_status == "succeeded":
                        if latest_charge_id and payment.stripe_charge_id != latest_charge_id:
                            payment.stripe_charge_id = latest_charge_id
                            payment.save(update_fields=["stripe_charge_id", "updated_at"])
                    else:
                        payment.stripe_status = "succeeded"
                        if latest_charge_id:
                            payment.stripe_charge_id = latest_charge_id
                            payment.save(
                                update_fields=["stripe_status", "stripe_charge_id", "updated_at"]
                            )
                        else:
                            payment.save(update_fields=["stripe_status", "updated_at"])

                    if not _ledger_already_final(payment.job_id):
                        try:
                            finalize_platform_ledger_for_job(
                                payment.job_id,
                                run_id=f"PAYMENT_INTENT_{intent_id}",
                            )
                        except ValidationError:
                            pass
            except ClientPayment.DoesNotExist:
                pass
        elif event_type == "payment_intent.payment_failed":
            intent = event["data"]["object"]
            intent_id = intent["id"]

            try:
                with transaction.atomic():
                    payment = (
                        ClientPayment.objects.select_for_update()
                        .get(stripe_payment_intent_id=intent_id)
                    )
                    if payment.stripe_status not in {"failed", "succeeded"}:
                        payment.stripe_status = "failed"
                        payment.save(update_fields=["stripe_status", "updated_at"])
            except ClientPayment.DoesNotExist:
                pass
        elif event_type == "charge.refunded":
            charge = event["data"]["object"]
            create_credit_notes_from_charge_refunded_event(charge)

        webhook_event.processing_status = "processed"
        webhook_event.save(update_fields=["processing_status"])
    except Exception as exc:
        webhook_event.processing_status = "error"
        webhook_event.error_message = str(exc)
        webhook_event.save(update_fields=["processing_status", "error_message"])

    return HttpResponse(status=200)
