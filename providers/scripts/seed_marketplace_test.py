import random
from django.db import transaction
from providers.models import Provider, ProviderService, ServiceCategory


@transaction.atomic
def run():

    print("Cleaning test providers...")

    ProviderService.objects.filter(provider__provider_id__gte=2000).delete()
    Provider.objects.filter(provider_id__gte=2000).delete()

    print("Creating providers...")

    category, _ = ServiceCategory.objects.get_or_create(
        id=1,
        defaults={"name": "Test Category", "slug": "test-category", "is_active": True},
    )

    for i in range(2000, 2030):

        rating = round(random.uniform(3.5, 5.0), 2)
        completed = random.randint(0, 300)
        cancelled = random.randint(0, min(50, completed))
        verified = random.choice([True, False])
        price = random.randint(8000, 15000)

        provider = Provider.objects.create(
            provider_id=i,
            provider_type="self_employed",
            contact_first_name="Test",
            contact_last_name=f"Provider{i}",
            phone_number=f"+1-555-010{i}",
            email=f"test-provider-{i}@example.com",
            province="QC",
            city="Laval",
            postal_code="H7A 0A1",
            address_line1="123 Test St",
            avg_rating=rating,
            completed_jobs_count=completed,
            cancelled_jobs_count=cancelled,
            is_verified=verified,
        )

        ProviderService.objects.create(
            provider=provider,
            category=category,
            custom_name="Test Service",
            description="Seeded for marketplace ranking tests.",
            billing_unit="hour",
            price_cents=price,
            is_active=True,
        )

    print("Done.")
