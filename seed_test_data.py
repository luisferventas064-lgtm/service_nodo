from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

from clients.models import Client
from providers.models import Provider, ProviderServiceArea
from service_type.models import ServiceType


CLIENT_EMAIL = "client_test@nodo.local"
CLIENT_PASSWORD = "TestNodo123!"
PROVIDER_PASSWORD = "Provider123!"

SERVICE_DEFINITIONS = [
    {
        "name": "Cleaning",
        "description": "Seeded cleaning service for NODO test flows.",
    },
    {
        "name": "Plumbing",
        "description": "Seeded plumbing service for NODO test flows.",
    },
    {
        "name": "Electrical",
        "description": "Seeded electrical service for NODO test flows.",
    },
]

PROVIDER_AREAS = [
    {
        "city": "Montreal",
        "province": "QC",
        "postal_prefix": "H2X",
        "postal_code": "H2X1A1",
    },
    {
        "city": "Laval",
        "province": "QC",
        "postal_prefix": "H7A",
        "postal_code": "H7A1A1",
    },
    {
        "city": "Longueuil",
        "province": "QC",
        "postal_prefix": "J4K",
        "postal_code": "J4K1A1",
    },
]


def ensure_client():
    now = timezone.now()
    client, created = Client.objects.get_or_create(
        email=CLIENT_EMAIL,
        defaults={
            "first_name": "Client",
            "last_name": "Test",
            "phone_number": "+15145550001",
            "password": make_password(CLIENT_PASSWORD),
            "is_phone_verified": True,
            "phone_verified_at": now,
            "accepts_terms": True,
            "profile_completed": True,
            "country": "Canada",
            "province": "QC",
            "city": "Montreal",
            "postal_code": "H2X1A1",
            "address_line1": "100 Test Client Ave",
            "languages_spoken": "English,French",
            "is_active": True,
        },
    )

    if not created:
        client.first_name = "Client"
        client.last_name = "Test"
        client.phone_number = "+15145550001"
        client.password = make_password(CLIENT_PASSWORD)
        client.is_phone_verified = True
        client.phone_verified_at = now
        client.accepts_terms = True
        client.country = "Canada"
        client.province = "QC"
        client.city = "Montreal"
        client.postal_code = "H2X1A1"
        client.address_line1 = "100 Test Client Ave"
        client.languages_spoken = "English,French"
        client.is_active = True
        client.save()

    client.evaluate_profile_completion()
    return client, created


def ensure_service_types():
    created_count = 0
    service_types = []

    for definition in SERVICE_DEFINITIONS:
        service_type, created = ServiceType.objects.get_or_create(
            name=definition["name"],
            defaults={
                "description": definition["description"],
                "is_active": True,
            },
        )
        if created:
            created_count += 1
        else:
            service_type.description = definition["description"]
            service_type.is_active = True
            service_type.save(update_fields=["description", "is_active", "updated_at"])
        service_types.append(service_type)

    return service_types, created_count


def ensure_provider(index):
    now = timezone.now()
    area = PROVIDER_AREAS[(index - 1) % len(PROVIDER_AREAS)]
    email = f"provider{index}@nodo.local"
    phone_number = f"+1514556{index:04d}"
    company_name = f"Provider Test {index}"

    provider, created = Provider.objects.get_or_create(
        email=email,
        defaults={
            "provider_type": Provider.TYPE_COMPANY,
            "company_name": company_name,
            "legal_name": company_name,
            "business_registration_number": f"NODO-TEST-{index:04d}",
            "contact_first_name": "Provider",
            "contact_last_name": f"Test {index}",
            "phone_number": phone_number,
            "password": make_password(PROVIDER_PASSWORD),
            "is_phone_verified": True,
            "phone_verified_at": now,
            "profile_completed": True,
            "accepts_terms": True,
            "billing_profile_completed": True,
            "service_area": area["city"],
            "country": "Canada",
            "province": area["province"],
            "city": area["city"],
            "postal_code": area["postal_code"],
            "address_line1": f"{200 + index} Test Provider Ave",
            "is_active": True,
        },
    )

    if not created:
        provider.provider_type = Provider.TYPE_COMPANY
        provider.company_name = company_name
        provider.legal_name = company_name
        provider.business_registration_number = f"NODO-TEST-{index:04d}"
        provider.contact_first_name = "Provider"
        provider.contact_last_name = f"Test {index}"
        provider.phone_number = phone_number
        provider.password = make_password(PROVIDER_PASSWORD)
        provider.is_phone_verified = True
        provider.phone_verified_at = now
        provider.accepts_terms = True
        provider.billing_profile_completed = True
        provider.service_area = area["city"]
        provider.country = "Canada"
        provider.province = area["province"]
        provider.city = area["city"]
        provider.postal_code = area["postal_code"]
        provider.address_line1 = f"{200 + index} Test Provider Ave"
        provider.is_active = True
        provider.save()

    ProviderServiceArea.objects.update_or_create(
        provider=provider,
        postal_prefix=area["postal_prefix"],
        defaults={
            "city": area["city"],
            "province": area["province"],
            "is_active": True,
        },
    )
    provider.evaluate_profile_completion()

    if not provider.billing_profile_completed:
        provider.billing_profile_completed = True
        provider.save(update_fields=["billing_profile_completed", "updated_at"])

    return provider, created


with transaction.atomic():
    client, client_created = ensure_client()
    service_types, created_service_count = ensure_service_types()

    created_providers = 0
    for provider_index in range(1, 21):
        _, created = ensure_provider(provider_index)
        if created:
            created_providers += 1

print(
    "Client created:" if client_created else "Client already existed:",
    client.email,
)
print(
    f"Services ready: {len(service_types)} total ({created_service_count} created in this run)"
)
print(f"Providers ready: 20 total ({created_providers} created in this run)")
print("Client login -> email:", CLIENT_EMAIL)
print("Client login -> password:", CLIENT_PASSWORD)
print("Provider login -> emails: provider1@nodo.local ... provider20@nodo.local")
print("Provider login -> password:", PROVIDER_PASSWORD)
