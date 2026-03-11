import random

from django.core.management.base import BaseCommand
from django.db import transaction

from providers.models import Provider, ProviderService, ProviderServiceArea
from providers.ranking import hydrate_provider_metrics, hydrate_provider_ranking_fields
from service_type.models import ServiceType

SEED_EMAIL_PREFIX = "seed-provider-"
SEED_SERVICE_TYPES = [
    {
        "name": "Seed Home Cleaning",
        "description": "Seeded service for marketplace and nearby coverage tests.",
    },
    {
        "name": "Seed Deep Cleaning",
        "description": "Seeded deep cleaning service for marketplace tests.",
    },
]
POSTAL_AREAS = (
    {"postal_prefix": "H2X", "city": "Montreal", "province": "QC"},
    {"postal_prefix": "H2Y", "city": "Montreal", "province": "QC"},
    {"postal_prefix": "H3A", "city": "Montreal", "province": "QC"},
    {"postal_prefix": "H7A", "city": "Laval", "province": "QC"},
)
BILLING_UNITS = ("fixed", "hour")


def _postal_code_from_prefix(postal_prefix: str, index: int) -> str:
    suffix = (index % 9) + 1
    return f"{postal_prefix}{suffix}A{suffix}"


class Command(BaseCommand):
    help = "Create seeded providers with coverage, services, metrics, and ranking data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=30,
            help="Number of providers to create. Defaults to 30.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=20260309,
            help="Random seed for deterministic output. Defaults to 20260309.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing seeded providers before creating new ones.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        count = max(1, int(options["count"]))
        seed = int(options["seed"])
        reset = bool(options["reset"])
        rng = random.Random(seed)

        if reset:
            deleted_count, _ = Provider.objects.filter(
                email__startswith=SEED_EMAIL_PREFIX
            ).delete()
            self.stdout.write(f"Deleted existing seed providers: {deleted_count}")

        service_types = []
        for definition in SEED_SERVICE_TYPES:
            service_type, _ = ServiceType.objects.get_or_create(
                name=definition["name"],
                defaults={
                    "description": definition["description"],
                    "is_active": True,
                },
            )
            if not service_type.is_active:
                service_type.is_active = True
                service_type.save(update_fields=["is_active", "updated_at"])
            service_types.append(service_type)

        start_index = Provider.objects.filter(email__startswith=SEED_EMAIL_PREFIX).count()
        created_count = 0

        for offset in range(count):
            provider_number = start_index + offset + 1
            area = POSTAL_AREAS[offset % len(POSTAL_AREAS)]
            avg_rating = round(rng.uniform(3.5, 5.0), 2)
            completed_jobs = rng.randint(10, 200)
            cancelled_jobs = rng.randint(0, min(20, completed_jobs))
            accepted_jobs = completed_jobs + cancelled_jobs + rng.randint(0, 25)
            average_response_time = round(rng.uniform(5.0, 45.0), 2)
            distance_score = round(rng.uniform(0.45, 1.0), 4)
            is_verified = rng.choice([True, False])

            provider = Provider.objects.create(
                provider_type=Provider.TYPE_COMPANY,
                company_name=f"Seed Provider {provider_number:03d}",
                legal_name=f"Seed Provider Legal {provider_number:03d}",
                business_registration_number=f"SEED-BR-{provider_number:05d}",
                contact_first_name="Seed",
                contact_last_name=f"Provider{provider_number:03d}",
                phone_number=f"5557{provider_number:06d}",
                email=f"{SEED_EMAIL_PREFIX}{provider_number:05d}@example.com",
                province=area["province"],
                city=area["city"],
                postal_code=_postal_code_from_prefix(area["postal_prefix"], provider_number),
                address_line1=f"{provider_number} Seed Test Ave",
                service_area=area["city"],
                is_phone_verified=True,
                profile_completed=True,
                billing_profile_completed=True,
                accepts_terms=True,
                is_active=True,
                is_verified=is_verified,
                avg_rating=avg_rating,
                completed_jobs_count=completed_jobs,
                cancelled_jobs_count=cancelled_jobs,
                distance_score=distance_score,
            )
            ProviderServiceArea.objects.create(
                provider=provider,
                city=area["city"],
                province=area["province"],
                postal_prefix=area["postal_prefix"],
                is_active=True,
            )

            for service_index, service_type in enumerate(service_types, start=1):
                ProviderService.objects.create(
                    provider=provider,
                    service_type=service_type,
                    custom_name=f"{service_type.name} Offer {service_index}",
                    description="Seeded provider service for marketplace validation.",
                    billing_unit=rng.choice(BILLING_UNITS),
                    price_cents=rng.randint(8000, 18000),
                    is_active=True,
                )

            metrics = provider.metrics
            metrics.jobs_completed = completed_jobs
            metrics.jobs_cancelled = cancelled_jobs
            metrics.jobs_accepted = accepted_jobs
            metrics.avg_response_time = average_response_time
            hydrate_provider_metrics(provider, metrics)
            metrics.save(
                update_fields=[
                    "jobs_completed",
                    "jobs_accepted",
                    "jobs_cancelled",
                    "avg_response_time",
                    "acceptance_rate",
                    "completion_rate",
                    "experience_score",
                    "operational_score",
                    "response_score",
                    "updated_at",
                ]
            )

            hydrate_provider_ranking_fields(provider, metrics)
            provider.save(
                update_fields=[
                    "avg_rating",
                    "distance_score",
                    "completed_jobs_count",
                    "cancelled_jobs_count",
                    "acceptance_rate",
                    "base_dispatch_score",
                    "hybrid_score",
                    "updated_at",
                ]
            )
            created_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {created_count} providers across {len(POSTAL_AREAS)} postal areas."
            )
        )
