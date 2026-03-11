from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from providers.models import Provider, ProviderCertificate, ProviderInsurance, ProviderService
from service_type.models import RequiredCertification, ServiceType

from .models import ComplianceRule
from .services import evaluate_provider_compliance, provider_meets_compliance


class ComplianceTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Compliance Provider",
            contact_first_name="Compliance",
            contact_last_name="Provider",
            phone_number="+15145550101",
            email="compliance.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1 Compliance St",
        )
        self.service_type = ServiceType.objects.create(
            name="Compliance Service",
            description="Compliance Service",
        )

    def test_provider_fails_when_required_certificate_is_missing(self):
        ComplianceRule.objects.create(
            province_code="QC",
            service_type=self.service_type,
            certificate_name="RBQ License",
            certificate_required=True,
            insurance_required=False,
            is_mandatory=True,
        )

        result = provider_meets_compliance(
            provider=self.provider,
            province_code="QC",
            service_type=self.service_type,
        )

        self.assertFalse(result["ok"])
        self.assertIn("RBQ License", result["missing_certificates"])
        self.assertFalse(result["missing_insurance"])

    def test_provider_passes_when_required_certificate_is_verified(self):
        ComplianceRule.objects.create(
            province_code="QC",
            service_type=self.service_type,
            certificate_name="RBQ License",
            certificate_required=True,
            insurance_required=False,
            is_mandatory=True,
        )
        ProviderCertificate.objects.create(
            provider=self.provider,
            cert_type="RBQ License",
            status=ProviderCertificate.Status.VERIFIED,
            expires_date=timezone.localdate() + timedelta(days=90),
        )

        result = provider_meets_compliance(
            provider=self.provider,
            province_code="QC",
            service_type=self.service_type,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["missing_certificates"], [])
        self.assertFalse(result["missing_insurance"])

    def test_provider_fails_when_required_insurance_is_missing(self):
        ComplianceRule.objects.create(
            province_code="QC",
            service_type=self.service_type,
            certificate_name="",
            certificate_required=False,
            insurance_required=True,
            is_mandatory=True,
        )

        result = provider_meets_compliance(
            provider=self.provider,
            province_code="QC",
            service_type=self.service_type,
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["missing_insurance"])
        self.assertTrue(result["insurance_required"])

    def test_non_mandatory_rule_does_not_block_provider(self):
        ComplianceRule.objects.create(
            province_code="QC",
            service_type=self.service_type,
            certificate_name="Optional Safety Card",
            certificate_required=True,
            insurance_required=False,
            is_mandatory=False,
        )
        ProviderInsurance.objects.create(
            provider=self.provider,
            has_insurance=True,
            is_verified=True,
            expiry_date=timezone.localdate() + timedelta(days=180),
        )

        result = provider_meets_compliance(
            provider=self.provider,
            province_code="QC",
            service_type=self.service_type,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["missing_certificates"], [])
        self.assertFalse(result["missing_insurance"])

    def test_provider_returns_missing_certificates_list(self):
        ComplianceRule.objects.create(
            province_code="QC",
            service_type=self.service_type,
            certificate_name="RBQ License",
            certificate_required=True,
            insurance_required=False,
            is_mandatory=True,
        )

        result = evaluate_provider_compliance(
            provider=self.provider,
            province_code="QC",
            service_type=self.service_type,
        )

        self.assertEqual(result["missing_certificates"], ["RBQ License"])
        self.assertFalse(result["is_compliant"])

    def test_provider_returns_insurance_required_flag(self):
        ComplianceRule.objects.create(
            province_code="QC",
            service_type=self.service_type,
            certificate_name="",
            certificate_required=False,
            insurance_required=True,
            is_mandatory=True,
        )
        ProviderInsurance.objects.create(
            provider=self.provider,
            has_insurance=True,
            is_verified=True,
            expiry_date=timezone.localdate() + timedelta(days=60),
        )

        result = evaluate_provider_compliance(
            provider=self.provider,
            province_code="QC",
            service_type=self.service_type,
        )

        self.assertTrue(result["insurance_required"])
        self.assertTrue(result["is_compliant"])

    def test_provider_is_compliant_when_verified_certificate_exists(self):
        ComplianceRule.objects.create(
            province_code="QC",
            service_type=self.service_type,
            certificate_name="RBQ License",
            certificate_required=True,
            insurance_required=False,
            is_mandatory=True,
        )
        ProviderCertificate.objects.create(
            provider=self.provider,
            cert_type="RBQ License",
            status=ProviderCertificate.Status.VERIFIED,
            expires_date=timezone.localdate() + timedelta(days=60),
        )

        result = evaluate_provider_compliance(
            provider=self.provider,
            province_code="QC",
            service_type=self.service_type,
        )

        self.assertTrue(result["is_compliant"])
        self.assertEqual(result["missing_certificates"], [])

    def test_provider_service_uses_legacy_required_certification_fallback(self):
        service = ProviderService.objects.create(
            provider=self.provider,
            service_type=self.service_type,
            custom_name="Compliance Service Offer",
            description="",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        RequiredCertification.objects.create(
            service_type=self.service_type,
            province="QC",
            requires_certificate=True,
            certificate_type="Legacy RBQ",
        )

        self.assertFalse(service.is_compliant)
