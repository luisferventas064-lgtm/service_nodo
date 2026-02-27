from datetime import datetime, time, timedelta, timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from clients.models import Client
from jobs.models import Job, PlatformLedgerEntry
from providers.models import Provider, ProviderUser
from service_type.models import ServiceType
from settlements.models import (
    JobDispute,
    LedgerAdjustment,
    ProviderSettlement,
    SettlementPayment,
    SettlementStatus,
)
from settlements.services import (
    approve_settlement,
    auto_resolve_expired_provider_response,
    execute_stripe_transfer,
    execute_settlement_payment,
    generate_weekly_settlements,
    generate_wednesday_payouts,
    provider_respond_dispute,
)

User = get_user_model()


class ProviderRespondDisputeTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Dispute Test Service",
            description="Dispute response service type",
        )
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _make_provider(self) -> Provider:
        seq = self._next_seq()
        return Provider.objects.create(
            provider_type="self_employed",
            contact_first_name=f"Provider{seq}",
            contact_last_name="Dispute",
            phone_number=f"555-700-00{seq:02d}",
            email=f"provider.dispute.{seq}@test.local",
            stripe_account_id=f"acct_test_{seq}",
            stripe_onboarding_completed=True,
            stripe_account_status="enabled",
            stripe_charges_enabled=True,
            stripe_payouts_enabled=True,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1=f"{seq} Provider St",
        )

    def _make_client(self) -> Client:
        seq = self._next_seq()
        return Client.objects.create(
            first_name=f"Client{seq}",
            last_name="Dispute",
            phone_number=f"555-800-00{seq:02d}",
            email=f"client.dispute.{seq}@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1=f"{seq} Client St",
        )

    def _make_job(self, provider: Provider, client: Client) -> Job:
        return Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            client=client,
            service_type=self.service_type,
            selected_provider=provider,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
        )

    def _make_dispute(
        self,
        *,
        status: str = JobDispute.Status.OPEN,
        provider_response: str | None = None,
    ) -> tuple[JobDispute, Provider]:
        provider = self._make_provider()
        client = self._make_client()
        job = self._make_job(provider=provider, client=client)
        dispute = JobDispute.objects.create(
            job=job,
            provider=provider,
            client=client,
            status=status,
            client_reason="Client reason",
            provider_response=provider_response,
        )
        return dispute, provider

    def _make_settlement_with_ledger(
        self,
        *,
        dispute_status: str = JobDispute.Status.OPEN,
        settlement_status: str = SettlementStatus.DRAFT,
        provider_response: str | None = None,
        scheduled_payout_date=None,
    ) -> tuple[JobDispute, ProviderSettlement]:
        dispute, provider = self._make_dispute(
            status=dispute_status,
            provider_response=provider_response,
        )

        now = timezone.now()
        settlement = ProviderSettlement.objects.create(
            provider=provider,
            period_start=now - timedelta(days=7),
            period_end=now,
            currency="CAD",
            status=settlement_status,
            scheduled_payout_date=scheduled_payout_date,
        )
        PlatformLedgerEntry.objects.create(
            job=dispute.job,
            settlement=settlement,
            is_final=True,
            finalized_at=now,
            gross_cents=10_000,
            tax_cents=1_000,
            fee_cents=500,
            net_provider_cents=9_500,
            platform_revenue_cents=500,
        )
        return dispute, settlement

    def test_provider_can_respond_inside_24h_window(self):
        dispute, provider = self._make_dispute()
        opened_at = timezone.now() - timedelta(hours=23, minutes=59)
        JobDispute.objects.filter(pk=dispute.pk).update(opened_at=opened_at)

        updated = provider_respond_dispute(
            dispute_id=dispute.pk,
            provider_id=provider.pk,
            response_text="I have evidence.",
        )

        self.assertEqual(updated.status, JobDispute.Status.PROVIDER_RESPONDED)
        self.assertEqual(updated.provider_response, "I have evidence.")
        self.assertIsNotNone(updated.provider_responded_at)

    def test_provider_can_respond_exactly_at_24h_deadline(self):
        dispute, provider = self._make_dispute()
        fixed_now = timezone.now()
        JobDispute.objects.filter(pk=dispute.pk).update(
            opened_at=fixed_now - timedelta(hours=24)
        )

        with patch("settlements.services.timezone.now", return_value=fixed_now):
            updated = provider_respond_dispute(
                dispute_id=dispute.pk,
                provider_id=provider.pk,
                response_text="Responding exactly at deadline.",
            )

        self.assertEqual(updated.status, JobDispute.Status.PROVIDER_RESPONDED)
        self.assertEqual(
            updated.provider_response,
            "Responding exactly at deadline.",
        )

    def test_provider_cannot_respond_after_24h_window(self):
        dispute, provider = self._make_dispute()
        opened_at = timezone.now() - timedelta(hours=24, seconds=1)
        JobDispute.objects.filter(pk=dispute.pk).update(opened_at=opened_at)

        with self.assertRaisesRegex(ValueError, "Response window expired"):
            provider_respond_dispute(
                dispute_id=dispute.pk,
                provider_id=provider.pk,
                response_text="Too late response.",
            )

    def test_provider_mismatch_is_rejected(self):
        dispute, _provider = self._make_dispute()
        other_provider = self._make_provider()

        with self.assertRaisesRegex(PermissionError, "Provider mismatch"):
            provider_respond_dispute(
                dispute_id=dispute.pk,
                provider_id=other_provider.pk,
                response_text="Wrong provider.",
            )

    def test_dispute_must_be_open(self):
        dispute, provider = self._make_dispute(status=JobDispute.Status.RESOLVED)

        with self.assertRaisesRegex(ValueError, "Dispute is not open"):
            provider_respond_dispute(
                dispute_id=dispute.pk,
                provider_id=provider.pk,
                response_text="Should not be accepted.",
            )

    def test_provider_response_is_immutable(self):
        dispute, provider = self._make_dispute(provider_response="First response")

        with self.assertRaisesRegex(ValueError, "Provider already responded"):
            provider_respond_dispute(
                dispute_id=dispute.pk,
                provider_id=provider.pk,
                response_text="Second response.",
            )


class SettlementDisputeGuardsTests(ProviderRespondDisputeTests):
    def test_approve_settlement_requires_provider_stripe_account(self):
        provider = self._make_provider()
        provider.stripe_account_id = None
        provider.save(update_fields=["stripe_account_id"])

        settlement = ProviderSettlement.objects.create(
            provider=provider,
            period_start=timezone.now() - timedelta(days=7),
            period_end=timezone.now(),
            currency="CAD",
            status=SettlementStatus.DRAFT,
        )

        with self.assertRaisesRegex(ValidationError, "Provider has no Stripe account."):
            approve_settlement(settlement)

    def test_approve_settlement_blocks_active_disputes(self):
        _dispute, settlement = self._make_settlement_with_ledger(
            dispute_status=JobDispute.Status.OPEN,
            settlement_status=SettlementStatus.DRAFT,
        )

        with self.assertRaisesRegex(ValueError, "Cannot close settlement with active disputes"):
            approve_settlement(settlement)

    def test_wednesday_payout_skips_settlement_with_active_disputes(self):
        _dispute, settlement = self._make_settlement_with_ledger(
            dispute_status=JobDispute.Status.OPEN,
            settlement_status=SettlementStatus.CLOSED,
            scheduled_payout_date=timezone.localdate() - timedelta(days=1),
        )

        processed = generate_wednesday_payouts()

        self.assertEqual(processed, [])
        settlement.refresh_from_db()
        self.assertEqual(settlement.status, SettlementStatus.CLOSED)
        self.assertIsNone(settlement.paid_at)

    def test_auto_resolve_expired_provider_response_resolves_refund_100(self):
        dispute, _settlement = self._make_settlement_with_ledger(
            dispute_status=JobDispute.Status.OPEN,
            settlement_status=SettlementStatus.DRAFT,
        )
        fixed_now = timezone.now()
        JobDispute.objects.filter(pk=dispute.pk).update(
            opened_at=fixed_now - timedelta(hours=24, minutes=1)
        )

        processed = auto_resolve_expired_provider_response(reference_time=fixed_now)

        self.assertIn(dispute.pk, processed)
        dispute.refresh_from_db()
        self.assertEqual(dispute.status, JobDispute.Status.RESOLVED)
        self.assertEqual(dispute.resolution_type, JobDispute.ResolutionType.REFUND_100)
        self.assertEqual(LedgerAdjustment.objects.filter(dispute=dispute).count(), 3)

    def test_auto_resolve_does_not_trigger_at_exact_24h_deadline(self):
        dispute, _settlement = self._make_settlement_with_ledger(
            dispute_status=JobDispute.Status.OPEN,
            settlement_status=SettlementStatus.DRAFT,
        )
        fixed_now = timezone.now()
        JobDispute.objects.filter(pk=dispute.pk).update(
            opened_at=fixed_now - timedelta(hours=24)
        )

        processed = auto_resolve_expired_provider_response(reference_time=fixed_now)

        self.assertNotIn(dispute.pk, processed)
        dispute.refresh_from_db()
        self.assertEqual(dispute.status, JobDispute.Status.OPEN)
        self.assertIsNone(dispute.resolution_type)

    def test_auto_resolve_skips_open_disputes_with_legacy_provider_response(self):
        dispute, _settlement = self._make_settlement_with_ledger(
            dispute_status=JobDispute.Status.OPEN,
            settlement_status=SettlementStatus.DRAFT,
            provider_response="Legacy response",
        )
        fixed_now = timezone.now()
        JobDispute.objects.filter(pk=dispute.pk).update(
            opened_at=fixed_now - timedelta(hours=25)
        )

        processed = auto_resolve_expired_provider_response(reference_time=fixed_now)

        self.assertNotIn(dispute.pk, processed)
        dispute.refresh_from_db()
        self.assertEqual(dispute.status, JobDispute.Status.OPEN)


class ProviderFinancialVisibilityTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Owner",
            contact_last_name="Provider",
            phone_number="555-600-0001",
            email="owner.provider@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Provider St",
        )
        self.url = f"/settlements/provider/{self.provider.provider_id}/financial-dashboard/"

    def _make_user(self, username: str, email: str, *, is_staff=False, is_superuser=False):
        return User.objects.create_user(
            username=username,
            email=email,
            password="testpass123",
            is_staff=is_staff,
            is_superuser=is_superuser,
        )

    def test_financial_dashboard_allows_superuser(self):
        user = self._make_user(
            "admin",
            "admin@test.local",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_financial_dashboard_allows_staff(self):
        user = self._make_user("staff", "staff@test.local", is_staff=True)
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_financial_dashboard_allows_provider_owner_email(self):
        user = self._make_user(
            "provider_owner",
            "owner.provider@test.local",
        )
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_financial_dashboard_rejects_non_admin_non_owner(self):
        user = self._make_user("worker", "worker@test.local")
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_financial_dashboard_rejects_provider_user_relation_without_owner_email(self):
        user = self._make_user("provider_finance", "finance.user@test.local")
        ProviderUser.objects.create(
            provider=self.provider,
            user=user,
            role="finance",
            is_active=True,
        )
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)


class SettlementPaymentExecutionTests(ProviderRespondDisputeTests):
    def setUp(self):
        super().setUp()
        self.actor_user = User.objects.create_user(
            username="finance_executor",
            email="finance.executor@test.local",
            password="testpass123",
        )

    def _make_settlement(self, *, status: str, total_net_provider_cents: int) -> ProviderSettlement:
        provider = self._make_provider()
        now = timezone.now()
        return ProviderSettlement.objects.create(
            provider=provider,
            period_start=now - timedelta(days=7),
            period_end=now,
            currency="CAD",
            total_net_provider_cents=total_net_provider_cents,
            status=status,
        )

    def test_execute_settlement_payment_rejects_non_approved_status(self):
        settlement = self._make_settlement(
            status=SettlementStatus.DRAFT,
            total_net_provider_cents=12_345,
        )

        with self.assertRaisesRegex(ValueError, "Settlement not approved"):
            execute_settlement_payment(
                settlement=settlement,
                user=self.actor_user,
                reference="BANK-REF-001",
            )

    def test_execute_settlement_payment_creates_record_and_marks_paid(self):
        settlement = self._make_settlement(
            status=SettlementStatus.CLOSED,
            total_net_provider_cents=98_765,
        )

        payment = execute_settlement_payment(
            settlement=settlement,
            user=self.actor_user,
            reference="BANK-REF-002",
        )

        settlement.refresh_from_db()
        self.assertEqual(settlement.status, SettlementStatus.PAID)
        self.assertIsNotNone(settlement.paid_at)
        self.assertEqual(payment.settlement_id, settlement.pk)
        self.assertEqual(payment.amount_cents, 98_765)
        self.assertEqual(payment.executed_by_id, self.actor_user.pk)
        self.assertEqual(payment.reference, "BANK-REF-002")

    def test_execute_settlement_payment_rejects_double_execution(self):
        settlement = self._make_settlement(
            status=SettlementStatus.CLOSED,
            total_net_provider_cents=55_000,
        )

        execute_settlement_payment(
            settlement=settlement,
            user=self.actor_user,
            reference="BANK-REF-003",
        )

        with self.assertRaisesRegex(ValueError, "Already paid"):
            execute_settlement_payment(
                settlement=settlement,
                user=self.actor_user,
                reference="BANK-REF-004",
            )
        self.assertEqual(SettlementPayment.objects.filter(settlement=settlement).count(), 1)

    def test_execute_settlement_payment_rolls_back_when_mark_paid_fails(self):
        settlement = self._make_settlement(
            status=SettlementStatus.CLOSED,
            total_net_provider_cents=77_000,
        )

        with patch("settlements.services.mark_settlement_paid", side_effect=RuntimeError("payment_posting_error")):
            with self.assertRaisesRegex(RuntimeError, "payment_posting_error"):
                execute_settlement_payment(
                    settlement=settlement,
                    user=self.actor_user,
                    reference="BANK-REF-005",
                )

        settlement.refresh_from_db()
        self.assertEqual(settlement.status, SettlementStatus.CLOSED)
        self.assertIsNone(settlement.paid_at)
        self.assertFalse(SettlementPayment.objects.filter(settlement=settlement).exists())

    @patch("settlements.services.get_stripe")
    def test_execute_stripe_transfer_creates_processing_payment(self, get_stripe_mock):
        settlement = self._make_settlement(
            status=SettlementStatus.CLOSED,
            total_net_provider_cents=12_500,
        )

        stripe_mock = MagicMock()
        transfer_mock = MagicMock()
        transfer_mock.id = "tr_test_123"
        stripe_mock.Transfer.create.return_value = transfer_mock
        get_stripe_mock.return_value = stripe_mock

        payment = execute_stripe_transfer(settlement, user=self.actor_user)

        self.assertEqual(payment.stripe_transfer_id, "tr_test_123")
        self.assertEqual(payment.stripe_idempotency_key, f"settlement_{settlement.pk}")
        self.assertEqual(payment.stripe_status, "processing")
        self.assertEqual(payment.amount_cents, 12_500)

    def test_execute_settlement_payment_requires_provider_stripe_ready(self):
        settlement = self._make_settlement(
            status=SettlementStatus.CLOSED,
            total_net_provider_cents=12_500,
        )
        provider = settlement.provider
        provider.stripe_payouts_enabled = False
        provider.save(update_fields=["stripe_payouts_enabled"])

        with self.assertRaisesRegex(ValidationError, "Provider payouts not enabled in Stripe."):
            execute_settlement_payment(
                settlement=settlement,
                user=self.actor_user,
                reference="BANK-REF-006",
            )


class SettlementWeeklyAdjustmentTests(ProviderRespondDisputeTests):
    def test_generate_weekly_settlements_includes_open_adjustment_ledgers(self):
        provider = self._make_provider()
        client = self._make_client()
        job = self._make_job(provider=provider, client=client)

        now = timezone.now()
        past_settlement = ProviderSettlement.objects.create(
            provider=provider,
            period_start=now - timedelta(days=30),
            period_end=now - timedelta(days=23),
            currency="CAD",
            status=SettlementStatus.PAID,
            paid_at=now - timedelta(days=22),
            total_gross_cents=10_000,
            total_tax_cents=0,
            total_fee_cents=0,
            total_net_provider_cents=10_000,
            total_platform_revenue_cents=0,
            total_jobs=1,
        )

        PlatformLedgerEntry.objects.create(
            job=job,
            settlement=past_settlement,
            is_final=True,
            is_adjustment=False,
            finalized_at=now - timedelta(days=22),
            gross_cents=10_000,
            tax_cents=0,
            fee_cents=0,
            net_provider_cents=10_000,
            platform_revenue_cents=0,
        )

        adjustment_finalized_at = now - timedelta(days=1)
        adjustment = PlatformLedgerEntry.objects.create(
            job=job,
            settlement=None,
            is_final=True,
            is_adjustment=True,
            finalized_at=adjustment_finalized_at,
            gross_cents=-2_000,
            tax_cents=0,
            fee_cents=0,
            net_provider_cents=-2_000,
            platform_revenue_cents=0,
            finalized_run_id="CREDIT_NOTE_re_weekly_001",
        )

        reference_date = (adjustment_finalized_at + timedelta(days=7)).date()
        created = generate_weekly_settlements(reference_date=reference_date)

        self.assertEqual(len(created), 1)
        settlement = created[0]
        self.assertEqual(settlement.provider_id, provider.pk)
        self.assertEqual(settlement.total_gross_cents, -2_000)
        self.assertEqual(settlement.total_tax_cents, 0)
        self.assertEqual(settlement.total_fee_cents, 0)
        self.assertEqual(settlement.total_net_provider_cents, -2_000)
        self.assertEqual(settlement.total_platform_revenue_cents, 0)
        self.assertEqual(settlement.status, SettlementStatus.DRAFT)

        adjustment.refresh_from_db()
        self.assertEqual(adjustment.settlement_id, settlement.pk)

        past_settlement.refresh_from_db()
        self.assertEqual(past_settlement.status, SettlementStatus.PAID)

    def test_generate_weekly_settlements_rejects_linking_ledgers_to_paid_period_settlement(self):
        provider = self._make_provider()
        client = self._make_client()
        job = self._make_job(provider=provider, client=client)

        reference_date = timezone.localdate()
        period_start_date = reference_date - timedelta(days=reference_date.weekday() + 7)
        period_end_date = period_start_date + timedelta(days=6)
        period_start_dt = timezone.make_aware(
            datetime.combine(period_start_date, time.min),
            dt_timezone.utc,
        )
        period_end_dt = timezone.make_aware(
            datetime.combine(period_end_date, time.max),
            dt_timezone.utc,
        )

        paid_settlement = ProviderSettlement.objects.create(
            provider=provider,
            period_start=period_start_dt,
            period_end=period_end_dt,
            currency="CAD",
            status=SettlementStatus.PAID,
            paid_at=timezone.now(),
            total_gross_cents=10_000,
            total_tax_cents=0,
            total_fee_cents=0,
            total_net_provider_cents=10_000,
            total_platform_revenue_cents=0,
            total_jobs=1,
        )

        adjustment_finalized_at = timezone.make_aware(
            datetime.combine(period_start_date + timedelta(days=2), time(hour=12)),
            dt_timezone.utc,
        )
        adjustment = PlatformLedgerEntry.objects.create(
            job=job,
            settlement=None,
            is_final=True,
            is_adjustment=True,
            finalized_at=adjustment_finalized_at,
            gross_cents=-1_500,
            tax_cents=0,
            fee_cents=0,
            net_provider_cents=-1_500,
            platform_revenue_cents=0,
            finalized_run_id="CREDIT_NOTE_re_guard_paid_001",
        )

        with self.assertRaisesRegex(ValidationError, "Cannot attach ledgers to immutable settlement"):
            generate_weekly_settlements(reference_date=reference_date)

        adjustment.refresh_from_db()
        self.assertIsNone(adjustment.settlement_id)
        paid_settlement.refresh_from_db()
        self.assertEqual(paid_settlement.status, SettlementStatus.PAID)

    def test_paid_settlement_is_immutable_on_save(self):
        provider = self._make_provider()
        now = timezone.now()
        settlement = ProviderSettlement.objects.create(
            provider=provider,
            period_start=now - timedelta(days=7),
            period_end=now,
            currency="CAD",
            status=SettlementStatus.PAID,
            paid_at=now,
            total_gross_cents=5_000,
            total_tax_cents=0,
            total_fee_cents=0,
            total_net_provider_cents=5_000,
            total_platform_revenue_cents=0,
            total_jobs=1,
        )

        settlement.notes = "mutated"
        with self.assertRaisesRegex(ValidationError, "Cannot modify a PAID settlement."):
            settlement.save()


class FinancialIntegrityCommandTests(TestCase):
    def test_financial_integrity_check_runs(self):
        call_command("financial_integrity_check")
