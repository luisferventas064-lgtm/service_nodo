from __future__ import annotations

from types import SimpleNamespace

from django.utils import timezone

from providers.models import ProviderCertificate
from service_type.models import RequiredCertification

from .models import ComplianceRule


def normalize_province_code(province_code):
    return (province_code or "").strip().upper()


def get_compliance_rules_for_service(province_code, service_type):
    return ComplianceRule.objects.filter(
        province_code=normalize_province_code(province_code),
        service_type=service_type,
    )


def _get_effective_rules(province_code, service_type):
    province_code = normalize_province_code(province_code)
    rules = list(get_compliance_rules_for_service(province_code, service_type))
    if rules:
        return rules

    legacy_rules = list(
        RequiredCertification.objects.filter(
            province=province_code,
            service_type=service_type,
        ).order_by("id")
    )
    return [
        SimpleNamespace(
            province_code=province_code,
            service_type=service_type,
            certificate_name=(rule.certificate_type or "").strip(),
            insurance_required=bool(rule.requires_insurance),
            certificate_required=bool(rule.requires_certificate),
            is_mandatory=True,
            notes="Legacy required certification rule",
        )
        for rule in legacy_rules
    ]


def _provider_has_verified_certificate(provider, certificate_name):
    today = timezone.localdate()
    return provider.certificates.filter(
        cert_type=certificate_name,
        status=ProviderCertificate.Status.VERIFIED,
    ).exclude(
        expires_date__lt=today,
    ).exists()


def _provider_has_verified_insurance(provider):
    insurance = getattr(provider, "insurance", None)
    if insurance is None:
        return False

    if not insurance.has_insurance or not insurance.is_verified:
        return False

    if insurance.expiry_date and insurance.expiry_date < timezone.localdate():
        return False

    return True


def evaluate_provider_compliance(provider, province_code, service_type):
    rules = _get_effective_rules(province_code, service_type)

    missing_certificates = []
    insurance_required = False
    missing_insurance = False

    for rule in rules:
        if rule.insurance_required:
            insurance_required = True

        if not rule.is_mandatory:
            continue

        if rule.certificate_required and rule.certificate_name:
            has_certificate = _provider_has_verified_certificate(
                provider,
                rule.certificate_name,
            )
            if not has_certificate and rule.certificate_name not in missing_certificates:
                missing_certificates.append(rule.certificate_name)

        if rule.insurance_required and not _provider_has_verified_insurance(provider):
            missing_insurance = True

    is_compliant = not missing_certificates and not missing_insurance

    return {
        "ok": is_compliant,
        "is_compliant": is_compliant,
        "missing_certificates": missing_certificates,
        "insurance_required": insurance_required,
        "missing_insurance": missing_insurance,
        "rules": rules,
    }


def provider_meets_compliance(provider, province_code, service_type):
    return evaluate_provider_compliance(provider, province_code, service_type)
