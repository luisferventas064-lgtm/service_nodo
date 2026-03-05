from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from clients.models import Client
from jobs.models import Job
from providers.models import (
    Provider,
    ProviderCertificate,
    ProviderInsurance,
    ProviderService,
    ProviderServiceArea,
)
from service_type.models import RequiredCertification, ServiceType


def run():
    print("Cleaning old demo data...")
    Job.objects.all().delete()
    Provider.objects.all().delete()
    Client.objects.all().delete()
    ServiceType.objects.all().delete()

    # -------------------------------------------------
    # SERVICE TYPES
    # -------------------------------------------------
    plumbing = ServiceType.objects.create(name="Plumbing")
    cleaning = ServiceType.objects.create(name="Cleaning")
    snow = ServiceType.objects.create(name="Snow Removal")

    RequiredCertification.objects.create(
        service_type=plumbing,
        province="Quebec",
        requires_certificate=True,
        certificate_type="RBQ",
        requires_insurance=True,
    )

    RequiredCertification.objects.create(
        service_type=cleaning,
        province="Quebec",
        requires_certificate=False,
        requires_insurance=False,
    )

    RequiredCertification.objects.create(
        service_type=snow,
        province="Quebec",
        requires_certificate=False,
        requires_insurance=True,
    )

    # -------------------------------------------------
    # CLIENTS
    # -------------------------------------------------
    clients = []
    for i in range(1, 6):
        c = Client.objects.create(
            first_name=f"Client{i}",
            last_name="Demo",
            phone_number=f"514000000{i}",
            email=f"client{i}@demo.com",
            country="Canada",
            province="Quebec",
            city="Laval",
            address_line1="123 Demo Street",
            postal_code="H7X1X1",
            accepts_terms=True,
            is_phone_verified=True,
            profile_completed=True,
        )
        clients.append(c)

    # -------------------------------------------------
    # PROVIDERS
    # -------------------------------------------------
    providers = []

    for i in range(1, 6):
        p = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name=f"Provider{i}",
            contact_last_name="Demo",
            phone_number=f"438000000{i}",
            email=f"provider{i}@demo.com",
            province="Quebec",
            city="Laval",
            postal_code="H7X2X2",
            address_line1="456 Provider Ave",
            is_phone_verified=True,
            accepts_terms=True,
            billing_profile_completed=True,
            profile_completed=True,
        )

        ProviderServiceArea.objects.create(
            provider=p,
            province="Quebec",
            city="Laval",
            is_active=True,
        )

        providers.append(p)

    # -------------------------------------------------
    # FULLY COMPLIANT PROVIDER (Provider 1)
    # -------------------------------------------------
    compliant_provider = providers[0]

    ProviderCertificate.objects.create(
        provider=compliant_provider,
        cert_type="RBQ",
        status="verified",
    )

    ProviderInsurance.objects.create(
        provider=compliant_provider,
        has_insurance=True,
        insurance_company="Intact",
        policy_number="POL123",
        coverage_amount=Decimal("2000000.00"),
        expiry_date=timezone.now().date() + timedelta(days=365),
        is_verified=True,
    )

    # -------------------------------------------------
    # SERVICES
    # -------------------------------------------------
    for p in providers:
        ProviderService.objects.create(
            provider=p,
            service_type=cleaning,
            custom_name="House Cleaning",
            billing_unit="hour",
            price_cents=5000,
        )

        ProviderService.objects.create(
            provider=p,
            service_type=plumbing,
            custom_name="Plumbing Repair",
            billing_unit="fixed",
            price_cents=15000,
            compliance_deadline=timezone.now().date() + timedelta(days=180),
        )

    # -------------------------------------------------
    # JOBS
    # -------------------------------------------------
    for i in range(6):
        Job.objects.create(
            client=clients[i % 5],
            service_type=cleaning,
            country="Canada",
            province="Quebec",
            city="Laval",
            postal_code="H7X1X1",
            address_line1="789 Job Street",
            job_mode=Job.JobMode.ON_DEMAND,
            is_asap=True,
            selected_provider=providers[i % 5],
        )

    print("Demo data created successfully.")
