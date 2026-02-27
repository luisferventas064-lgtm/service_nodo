from unittest.mock import MagicMock, patch
from datetime import timedelta

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from clients.models import Client, ClientTicket
from jobs.models import Job, PlatformLedgerEntry
from payments.models import ClientCreditNote, ClientPayment, StripeWebhookEvent
from payments.services import create_payment_intent_for_job
from providers.models import Provider
from service_type.models import ServiceType
from settlements.models import (
    ProviderSettlement,
    SettlementPayment,
    SettlementStatus,
)

User = get_user_model()


class StripeWebhookTests(TestCase):
    def _make_client(self, suffix: str = "webhook") -> Client:
        return Client.objects.create(
            first_name="Client",
            last_name=f"{suffix}",
            phone_number="555-999-1000",
            email=f"{suffix}@client.test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )

    def _make_job(self, suffix: str = "webhook") -> Job:
        service_type = ServiceType.objects.create(
            name=f"Webhook Service {suffix}",
            description="Service type for webhook tests",
        )
        client = self._make_client(suffix=suffix)
        return Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            client=client,
            service_type=service_type,
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Job St",
        )

    def _make_client_payment(self, *, intent_id: str, status: str = "created") -> ClientPayment:
        job = self._make_job(suffix=intent_id)
        return ClientPayment.objects.create(
            job=job,
            stripe_payment_intent_id=intent_id,
            amount_cents=10_000,
            stripe_status=status,
            stripe_environment=settings.STRIPE_MODE,
        )

    def _make_provider(self, *, stripe_account_id: str) -> Provider:
        return Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Webhook",
            contact_last_name="Provider",
            phone_number="555-910-0001",
            email=f"{stripe_account_id}@test.local",
            stripe_account_id=stripe_account_id,
            stripe_onboarding_completed=False,
            stripe_payouts_enabled=False,
            stripe_charges_enabled=False,
            stripe_account_status="pending",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Webhook St",
        )

    def _make_payment_with_transfer(self, *, transfer_id: str) -> SettlementPayment:
        provider = self._make_provider(stripe_account_id="acct_payment_webhook_1")
        actor = User.objects.create_user(
            username=f"actor_{transfer_id}",
            email=f"{transfer_id}@actor.test.local",
            password="testpass123",
        )
        now = timezone.now()
        settlement = ProviderSettlement.objects.create(
            provider=provider,
            period_start=now - timedelta(days=7),
            period_end=now,
            currency="CAD",
            total_net_provider_cents=15_000,
            status=SettlementStatus.CLOSED,
        )
        return SettlementPayment.objects.create(
            settlement=settlement,
            executed_at=now,
            executed_by=actor,
            reference=f"stripe_transfer:{transfer_id}",
            amount_cents=15_000,
            stripe_transfer_id=transfer_id,
            stripe_status="processing",
        )

    def _make_refundable_context(
        self,
        *,
        intent_id: str,
        charge_id: str,
        amount_cents: int = 10_000,
        settlement_status: str = SettlementStatus.CLOSED,
        ledger_gross_cents: int | None = None,
        ledger_tax_cents: int = 0,
        ledger_fee_cents: int = 0,
        ledger_net_provider_cents: int | None = None,
        ledger_platform_revenue_cents: int = 0,
    ):
        provider = self._make_provider(stripe_account_id=f"acct_refund_{intent_id}")
        now = timezone.now()
        job = self._make_job(suffix=intent_id)
        job.selected_provider = provider
        job.save(update_fields=["selected_provider"])

        ticket = ClientTicket.objects.create(
            client=job.client,
            ref_type="job",
            ref_id=job.pk,
            ticket_no=f"CLNT-RF-{job.pk}",
            stage=ClientTicket.Stage.FINAL,
            status=ClientTicket.Status.FINALIZED,
            subtotal_cents=amount_cents,
            tax_cents=0,
            total_cents=amount_cents,
            currency="CAD",
            tax_region_code="CA-QC",
        )

        payment = ClientPayment.objects.create(
            job=job,
            stripe_payment_intent_id=intent_id,
            stripe_charge_id=charge_id,
            amount_cents=amount_cents,
            stripe_status="succeeded",
            stripe_environment=settings.STRIPE_MODE,
        )

        settlement_kwargs = {
            "provider": provider,
            "period_start": now - timedelta(days=7),
            "period_end": now,
            "currency": "CAD",
            "total_net_provider_cents": (
                ledger_net_provider_cents
                if ledger_net_provider_cents is not None
                else amount_cents
            ),
            "status": settlement_status,
        }
        if settlement_status == SettlementStatus.PAID:
            settlement_kwargs["paid_at"] = now
        settlement = ProviderSettlement.objects.create(**settlement_kwargs)

        gross_cents = ledger_gross_cents if ledger_gross_cents is not None else amount_cents
        net_provider_cents = (
            ledger_net_provider_cents
            if ledger_net_provider_cents is not None
            else amount_cents
        )
        ledger = PlatformLedgerEntry.objects.create(
            job=job,
            settlement=settlement,
            is_final=True,
            finalized_at=now,
            gross_cents=gross_cents,
            tax_cents=ledger_tax_cents,
            fee_cents=ledger_fee_cents,
            net_provider_cents=net_provider_cents,
            platform_revenue_cents=ledger_platform_revenue_cents,
        )
        return payment, ticket, settlement, ledger

    @patch("payments.views.stripe.Webhook.construct_event")
    def test_invalid_signature_returns_400(self, construct_event_mock):
        construct_event_mock.side_effect = stripe.error.SignatureVerificationError(
            "bad signature",
            "t=1,v1=bad",
        )

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=bad",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content.decode(), "Invalid signature")
        self.assertEqual(StripeWebhookEvent.objects.count(), 0)

    @patch("payments.views.stripe.Webhook.construct_event")
    def test_account_updated_updates_provider_flags_and_records_event(self, construct_event_mock):
        provider = self._make_provider(stripe_account_id="acct_test_webhook_1")
        construct_event_mock.return_value = {
            "id": "evt_1",
            "type": "account.updated",
            "data": {
                "object": {
                    "id": "acct_test_webhook_1",
                    "details_submitted": True,
                    "payouts_enabled": True,
                    "charges_enabled": True,
                }
            },
        }

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(response.status_code, 200)
        provider.refresh_from_db()
        self.assertTrue(provider.stripe_onboarding_completed)
        self.assertTrue(provider.stripe_payouts_enabled)
        self.assertTrue(provider.stripe_charges_enabled)
        self.assertEqual(provider.stripe_account_status, "active")

        audit = StripeWebhookEvent.objects.get(event_id="evt_1")
        self.assertEqual(audit.event_type, "account.updated")
        self.assertEqual(audit.stripe_account_id, "acct_test_webhook_1")
        self.assertEqual(audit.processing_status, "processed")
        self.assertIsNone(audit.error_message)

    @patch("payments.views.stripe.Webhook.construct_event")
    def test_duplicate_event_is_idempotent(self, construct_event_mock):
        self._make_provider(stripe_account_id="acct_test_webhook_2")
        construct_event_mock.return_value = {
            "id": "evt_2",
            "type": "account.updated",
            "data": {
                "object": {
                    "id": "acct_test_webhook_2",
                    "details_submitted": True,
                    "payouts_enabled": True,
                    "charges_enabled": True,
                }
            },
        }

        r1 = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )
        r2 = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(StripeWebhookEvent.objects.filter(event_id="evt_2").count(), 1)

    @patch("payments.views.Provider.objects.select_for_update")
    @patch("payments.views.stripe.Webhook.construct_event")
    def test_processing_exception_marks_event_error(
        self,
        construct_event_mock,
        provider_select_for_update_mock,
    ):
        construct_event_mock.return_value = {
            "id": "evt_3",
            "type": "account.updated",
            "data": {"object": {"id": "acct_test_webhook_3"}},
        }
        provider_select_for_update_mock.return_value.get.side_effect = RuntimeError(
            "database_error"
        )

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(response.status_code, 200)
        audit = StripeWebhookEvent.objects.get(event_id="evt_3")
        self.assertEqual(audit.processing_status, "error")
        self.assertIn("database_error", audit.error_message)

    @patch("payments.views.stripe.Webhook.construct_event")
    def test_account_updated_unknown_provider_returns_200_and_processed(self, construct_event_mock):
        construct_event_mock.return_value = {
            "id": "evt_4",
            "type": "account.updated",
            "data": {"object": {"id": "acct_unknown"}},
        }

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(response.status_code, 200)
        audit = StripeWebhookEvent.objects.get(event_id="evt_4")
        self.assertEqual(audit.processing_status, "processed")
        self.assertEqual(audit.stripe_account_id, "acct_unknown")

    @patch("payments.views.stripe.Webhook.construct_event")
    def test_non_account_updated_event_is_noop_but_audited(self, construct_event_mock):
        self._make_provider(stripe_account_id="acct_test_webhook_5")
        construct_event_mock.return_value = {
            "id": "evt_5",
            "type": "payout.paid",
            "data": {"object": {"id": "po_test_1"}},
        }

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(response.status_code, 200)
        audit = StripeWebhookEvent.objects.get(event_id="evt_5")
        self.assertEqual(audit.processing_status, "processed")
        self.assertEqual(audit.event_type, "payout.paid")

    @patch("payments.views.stripe.Webhook.construct_event")
    def test_transfer_paid_updates_settlement_payment_status(self, construct_event_mock):
        payment = self._make_payment_with_transfer(transfer_id="tr_123")
        construct_event_mock.return_value = {
            "id": "evt_transfer_paid_1",
            "type": "transfer.paid",
            "data": {"object": {"id": "tr_123", "paid": True}},
        }

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.stripe_status, "success")
        self.assertIsNone(payment.stripe_failure_reason)

    @patch("payments.views.stripe.Webhook.construct_event")
    def test_transfer_failed_updates_settlement_payment_status_and_reason(self, construct_event_mock):
        payment = self._make_payment_with_transfer(transfer_id="tr_456")
        construct_event_mock.return_value = {
            "id": "evt_transfer_failed_1",
            "type": "transfer.failed",
            "data": {
                "object": {
                    "id": "tr_456",
                    "paid": False,
                    "failure_message": "insufficient_funds",
                }
            },
        }

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.stripe_status, "failed")
        self.assertEqual(payment.stripe_failure_reason, "insufficient_funds")

    @patch("payments.views.finalize_platform_ledger_for_job")
    @patch("payments.views.stripe.Webhook.construct_event")
    def test_payment_intent_succeeded_updates_client_payment_and_finalizes_ledger(
        self,
        construct_event_mock,
        finalize_ledger_mock,
    ):
        payment = self._make_client_payment(intent_id="pi_success_001")
        construct_event_mock.return_value = {
            "id": "evt_pi_succeeded_1",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_success_001"}},
        }

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.stripe_status, "succeeded")
        finalize_ledger_mock.assert_called_once_with(
            payment.job_id,
            run_id="PAYMENT_INTENT_pi_success_001",
        )

    @patch("payments.views.stripe.Webhook.construct_event")
    def test_payment_intent_failed_updates_client_payment_status(self, construct_event_mock):
        payment = self._make_client_payment(intent_id="pi_failed_001")
        construct_event_mock.return_value = {
            "id": "evt_pi_failed_1",
            "type": "payment_intent.payment_failed",
            "data": {"object": {"id": "pi_failed_001"}},
        }

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.stripe_status, "failed")

    @patch("payments.views.stripe.Webhook.construct_event")
    def test_charge_refunded_creates_credit_note_and_compensating_ledger_entry(
        self,
        construct_event_mock,
    ):
        payment, ticket, settlement, ledger = self._make_refundable_context(
            intent_id="pi_refunded_001",
            charge_id="ch_refunded_001",
            amount_cents=10_000,
            settlement_status=SettlementStatus.CLOSED,
        )
        construct_event_mock.return_value = {
            "id": "evt_charge_refunded_1",
            "type": "charge.refunded",
            "data": {
                "object": {
                    "id": "ch_refunded_001",
                    "payment_intent": "pi_refunded_001",
                    "amount_refunded": 3_000,
                    "refunds": {
                        "data": [
                            {"id": "re_001", "amount": 3_000, "reason": "requested_by_customer"}
                        ]
                    },
                }
            },
        }

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(response.status_code, 200)
        credit_note = ClientCreditNote.objects.get(stripe_refund_id="re_001")
        self.assertEqual(credit_note.ticket_id, ticket.pk)
        self.assertEqual(credit_note.client_payment_id, payment.pk)
        self.assertEqual(credit_note.amount_cents, 3_000)
        self.assertEqual(credit_note.currency, "CAD")
        self.assertEqual(credit_note.stripe_environment, settings.STRIPE_MODE)

        settlement.refresh_from_db()
        self.assertEqual(settlement.status, SettlementStatus.CLOSED)
        self.assertEqual(settlement.total_net_provider_cents, 10_000)

        adjustment_ledger = PlatformLedgerEntry.objects.get(
            job=payment.job,
            is_adjustment=True,
            settlement__isnull=True,
            finalized_run_id="CREDIT_NOTE_re_001",
        )
        self.assertEqual(adjustment_ledger.gross_cents, -3_000)
        self.assertEqual(adjustment_ledger.tax_cents, 0)
        self.assertEqual(adjustment_ledger.fee_cents, 0)
        self.assertEqual(adjustment_ledger.net_provider_cents, -3_000)
        self.assertEqual(adjustment_ledger.platform_revenue_cents, 0)
        self.assertTrue(adjustment_ledger.is_final)
        self.assertIsNone(adjustment_ledger.settlement_id)

        ledger.refresh_from_db()
        self.assertEqual(ledger.settlement_id, settlement.pk)

    @patch("payments.views.stripe.Webhook.construct_event")
    def test_charge_refunded_paid_settlement_creates_future_adjustment(self, construct_event_mock):
        payment, _ticket, settlement, _ledger = self._make_refundable_context(
            intent_id="pi_refunded_paid_001",
            charge_id="ch_refunded_paid_001",
            amount_cents=8_000,
            settlement_status=SettlementStatus.PAID,
        )
        construct_event_mock.return_value = {
            "id": "evt_charge_refunded_paid_1",
            "type": "charge.refunded",
            "data": {
                "object": {
                    "id": "ch_refunded_paid_001",
                    "payment_intent": "pi_refunded_paid_001",
                    "amount_refunded": 2_000,
                    "refunds": {
                        "data": [
                            {"id": "re_paid_001", "amount": 2_000, "reason": "requested_by_customer"}
                        ]
                    },
                }
            },
        }

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(ClientCreditNote.objects.filter(stripe_refund_id="re_paid_001").exists())

        settlement.refresh_from_db()
        self.assertEqual(settlement.status, SettlementStatus.PAID)
        self.assertEqual(settlement.total_net_provider_cents, 8_000)

        adjustment_ledger = PlatformLedgerEntry.objects.get(
            job=payment.job,
            is_adjustment=True,
            finalized_run_id="CREDIT_NOTE_re_paid_001",
        )
        self.assertEqual(adjustment_ledger.gross_cents, -2_000)
        self.assertEqual(adjustment_ledger.net_provider_cents, -2_000)
        self.assertIsNone(adjustment_ledger.settlement_id)

    @patch("payments.views.stripe.Webhook.construct_event")
    def test_charge_refunded_rounding_residual_is_absorbed_by_platform(self, construct_event_mock):
        payment, _ticket, _settlement, _ledger = self._make_refundable_context(
            intent_id="pi_refunded_residual_001",
            charge_id="ch_refunded_residual_001",
            amount_cents=1_000,
            settlement_status=SettlementStatus.PAID,
            ledger_gross_cents=1_000,
            ledger_tax_cents=100,
            ledger_fee_cents=199,
            ledger_net_provider_cents=701,
            ledger_platform_revenue_cents=199,
        )
        construct_event_mock.return_value = {
            "id": "evt_charge_refunded_residual_1",
            "type": "charge.refunded",
            "data": {
                "object": {
                    "id": "ch_refunded_residual_001",
                    "payment_intent": "pi_refunded_residual_001",
                    "amount_refunded": 333,
                    "refunds": {
                        "data": [
                            {
                                "id": "re_residual_001",
                                "amount": 333,
                                "reason": "requested_by_customer",
                            }
                        ]
                    },
                }
            },
        }

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(ClientCreditNote.objects.filter(stripe_refund_id="re_residual_001").exists())

        adjustment_ledger = PlatformLedgerEntry.objects.get(
            job=payment.job,
            is_adjustment=True,
            finalized_run_id="CREDIT_NOTE_re_residual_001",
        )
        self.assertEqual(adjustment_ledger.gross_cents, -333)
        self.assertEqual(adjustment_ledger.net_provider_cents, -233)
        self.assertEqual(adjustment_ledger.platform_revenue_cents, -67)
        self.assertEqual(adjustment_ledger.fee_cents, -67)
        self.assertEqual(adjustment_ledger.tax_cents, -33)
        self.assertEqual(
            adjustment_ledger.net_provider_cents
            + adjustment_ledger.platform_revenue_cents
            + adjustment_ledger.tax_cents,
            -333,
        )

    @patch("payments.views.stripe.Webhook.construct_event")
    def test_charge_refunded_rejects_amount_above_paid_and_marks_event_error(self, construct_event_mock):
        _payment, _ticket, _settlement, _ledger = self._make_refundable_context(
            intent_id="pi_refunded_limit_001",
            charge_id="ch_refunded_limit_001",
            amount_cents=5_000,
            settlement_status=SettlementStatus.CLOSED,
        )
        construct_event_mock.return_value = {
            "id": "evt_charge_refunded_limit_1",
            "type": "charge.refunded",
            "data": {
                "object": {
                    "id": "ch_refunded_limit_001",
                    "payment_intent": "pi_refunded_limit_001",
                    "amount_refunded": 6_000,
                    "refunds": {
                        "data": [
                            {"id": "re_limit_001", "amount": 6_000, "reason": "requested_by_customer"}
                        ]
                    },
                }
            },
        }

        response = self.client.post(
            "/api/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ClientCreditNote.objects.filter(stripe_refund_id="re_limit_001").exists())
        audit = StripeWebhookEvent.objects.get(event_id="evt_charge_refunded_limit_1")
        self.assertEqual(audit.processing_status, "error")
        self.assertIn("Refund exceeds total paid amount", audit.error_message)


class ClientPaymentIntentServiceTests(TestCase):
    def _make_client(self, suffix: str) -> Client:
        return Client.objects.create(
            first_name="Client",
            last_name=suffix,
            phone_number="555-888-1000",
            email=f"{suffix}@service.test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Service St",
        )

    def _make_job(self, suffix: str) -> Job:
        service_type = ServiceType.objects.create(
            name=f"Service Intent {suffix}",
            description="Service type for intent tests",
        )
        client = self._make_client(suffix=suffix)
        return Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            client=client,
            service_type=service_type,
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Payment St",
        )

    @patch("payments.services.get_stripe")
    def test_create_payment_intent_for_job_creates_client_payment(self, get_stripe_mock):
        job = self._make_job("create_intent")
        ClientTicket.objects.create(
            client=job.client,
            ref_type="job",
            ref_id=job.pk,
            ticket_no="CLNT-1-00000001",
            stage=ClientTicket.Stage.FINAL,
            status=ClientTicket.Status.FINALIZED,
            subtotal_cents=9_000,
            tax_cents=1_000,
            total_cents=10_000,
            currency="CAD",
            tax_region_code="CA-QC",
        )

        stripe_mock = MagicMock()
        intent_mock = MagicMock()
        intent_mock.id = "pi_create_001"
        intent_mock.client_secret = "cs_test_123"
        stripe_mock.PaymentIntent.create.return_value = intent_mock
        get_stripe_mock.return_value = stripe_mock

        client_secret = create_payment_intent_for_job(job)

        self.assertEqual(client_secret, "cs_test_123")
        payment = ClientPayment.objects.get(stripe_payment_intent_id="pi_create_001")
        self.assertEqual(payment.job_id, job.pk)
        self.assertEqual(payment.amount_cents, 10_000)
        self.assertEqual(payment.stripe_status, "created")
        self.assertEqual(payment.stripe_environment, settings.STRIPE_MODE)

        stripe_mock.PaymentIntent.create.assert_called_once()
        _, kwargs = stripe_mock.PaymentIntent.create.call_args
        self.assertEqual(kwargs["amount"], 10_000)
        self.assertEqual(kwargs["currency"], "cad")
        self.assertEqual(kwargs["metadata"]["job_id"], job.pk)

    @patch("payments.services.get_stripe")
    def test_create_payment_intent_for_job_raises_when_no_final_ticket(
        self,
        get_stripe_mock,
    ):
        job = self._make_job("no_amount")
        get_stripe_mock.return_value = MagicMock()

        with self.assertRaisesRegex(ValidationError, "No final client ticket available for payment"):
            create_payment_intent_for_job(job)

    @patch("payments.services.get_stripe")
    def test_create_payment_intent_for_job_rejects_non_finalized_final_ticket(
        self,
        get_stripe_mock,
    ):
        job = self._make_job("non_finalized")
        ClientTicket.objects.create(
            client=job.client,
            ref_type="job",
            ref_id=job.pk,
            ticket_no="CLNT-1-00000002",
            stage=ClientTicket.Stage.FINAL,
            status=ClientTicket.Status.OPEN,
            subtotal_cents=9_000,
            tax_cents=1_000,
            total_cents=10_000,
            currency="CAD",
            tax_region_code="CA-QC",
        )
        get_stripe_mock.return_value = MagicMock()

        with self.assertRaisesRegex(ValidationError, "No final client ticket available for payment"):
            create_payment_intent_for_job(job)
