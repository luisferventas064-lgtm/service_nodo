from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.db import transaction
from django.db.models import Q

from providers.models import Provider
from providers.models import ProviderService
from providers.models import ServiceCategory
from providers.models import ServiceZone


CATEGORY_DEFINITIONS = [
    {
        "id": 1,
        "name": "Marketplace Test Category 1",
        "slug": "marketplace-test-category-1",
    },
    {
        "id": 2,
        "name": "Marketplace Test Category 2",
        "slug": "marketplace-test-category-2",
    },
]

ZONE_DEFINITIONS = [
    ("QC", "Montreal", "Downtown"),
    ("QC", "Montreal", "Plateau"),
    ("QC", "Montreal", "Rosemont"),
    ("QC", "Montreal", "West Island"),
    ("QC", "Montreal", "Old Montreal"),
    ("QC", "Laval", "Chomedey"),
    ("QC", "Laval", "Laval-des-Rapides"),
    ("QC", "Laval", "Sainte-Rose"),
    ("ON", "Toronto", "Downtown"),
    ("ON", "Toronto", "North York"),
    ("ON", "Toronto", "Scarborough"),
    ("ON", "Toronto", "Etobicoke"),
    ("ON", "Toronto", "Midtown"),
]

SLICE_DEFINITIONS = [
    {
        "id_base": 3000,
        "label": "qc-laval-cat1",
        "category_id": 1,
        "province": "QC",
        "city": "Laval",
        "count": 20,
        "profile_offset": 0,
        "rating_offset": 0.00,
        "completed_offset": 0,
        "cancelled_offset": 0,
        "price_offset": 0,
        "acceptance_offset": 0.0,
    },
    {
        "id_base": 3100,
        "label": "qc-montreal-cat1",
        "category_id": 1,
        "province": "QC",
        "city": "Montreal",
        "count": 20,
        "profile_offset": 5,
        "rating_offset": 0.03,
        "completed_offset": 18,
        "cancelled_offset": 1,
        "price_offset": 250,
        "acceptance_offset": 1.0,
    },
    {
        "id_base": 3200,
        "label": "qc-laval-cat2",
        "category_id": 2,
        "province": "QC",
        "city": "Laval",
        "count": 15,
        "profile_offset": 10,
        "rating_offset": -0.04,
        "completed_offset": -18,
        "cancelled_offset": 2,
        "price_offset": 450,
        "acceptance_offset": -2.0,
    },
    {
        "id_base": 3300,
        "label": "qc-montreal-cat2",
        "category_id": 2,
        "province": "QC",
        "city": "Montreal",
        "count": 15,
        "profile_offset": 15,
        "rating_offset": 0.01,
        "completed_offset": 10,
        "cancelled_offset": 1,
        "price_offset": 650,
        "acceptance_offset": 0.5,
    },
    {
        "id_base": 3400,
        "label": "on-toronto-cat1",
        "category_id": 1,
        "province": "ON",
        "city": "Toronto",
        "count": 20,
        "profile_offset": 3,
        "rating_offset": 0.02,
        "completed_offset": 22,
        "cancelled_offset": 1,
        "price_offset": 900,
        "acceptance_offset": 1.5,
    },
    {
        "id_base": 3500,
        "label": "on-toronto-cat2",
        "category_id": 2,
        "province": "ON",
        "city": "Toronto",
        "count": 20,
        "profile_offset": 8,
        "rating_offset": -0.02,
        "completed_offset": 8,
        "cancelled_offset": 2,
        "price_offset": 1050,
        "acceptance_offset": -0.5,
    },
]


def build_profiles():
    profiles = []

    for index in range(5):
        profiles.append(
            {
                "segment": "premium_verified",
                "rating": 4.72 + (index * 0.03),
                "completed": 180 + (index * 22),
                "cancelled": 2 + index,
                "verified": True,
                "price_cents": 13200 + (index * 520),
                "acceptance_rate": 94.0 + index,
            }
        )

    for index in range(5):
        profiles.append(
            {
                "segment": "value_nonverified",
                "rating": 4.12 + (index * 0.06),
                "completed": 145 + (index * 16),
                "cancelled": 9 + (index * 2),
                "verified": False,
                "price_cents": 8300 + (index * 240),
                "acceptance_rate": 82.0 + (index * 2),
            }
        )

    for index in range(5):
        profiles.append(
            {
                "segment": "merit_nonverified",
                "rating": 4.54 + (index * 0.05),
                "completed": 205 + (index * 18),
                "cancelled": index,
                "verified": False,
                "price_cents": 10850 + (index * 310),
                "acceptance_rate": 90.0 + index,
            }
        )

    for index in range(5):
        profiles.append(
            {
                "segment": "verified_value",
                "rating": 4.36 + (index * 0.05),
                "completed": 95 + (index * 15),
                "cancelled": 7 + (index * 2),
                "verified": True,
                "price_cents": 9200 + (index * 210),
                "acceptance_rate": 86.0 + (index * 1.5),
            }
        )

    return profiles


def ensure_categories():
    categories = {}
    for definition in CATEGORY_DEFINITIONS:
        category, _ = ServiceCategory.objects.update_or_create(
            id=definition["id"],
            defaults={
                "name": definition["name"],
                "slug": definition["slug"],
                "is_active": True,
            },
        )
        categories[definition["id"]] = category
    return categories


def ensure_zones():
    zones_by_city = {}
    for province, city, name in ZONE_DEFINITIONS:
        zone, _ = ServiceZone.objects.update_or_create(
            province=province,
            city=city,
            name=name,
        )
        zones_by_city.setdefault((province, city), []).append(zone)

    for city_zones in zones_by_city.values():
        city_zones.sort(key=lambda zone: zone.name)

    return zones_by_city


def delete_existing_seed_data():
    seed_q = Q(email__startswith="test-provider-") | Q(email__startswith="marketplace-seed-")
    provider_count = Provider.objects.filter(seed_q).count()
    deleted_objects, _ = Provider.objects.filter(seed_q).delete()
    return provider_count, deleted_objects


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def create_slice(*, definition, category, profiles, zones_by_city):
    created = 0
    slice_zones = zones_by_city.get((definition["province"], definition["city"]), [])
    for index in range(definition["count"]):
        profile = profiles[(index + definition["profile_offset"]) % len(profiles)]
        provider_id = definition["id_base"] + index

        rating = round(clamp(profile["rating"] + definition["rating_offset"], 3.5, 4.99), 2)
        completed = max(0, profile["completed"] + definition["completed_offset"])
        cancelled = max(0, profile["cancelled"] + definition["cancelled_offset"])
        cancelled = min(cancelled, completed)
        price_cents = max(5000, profile["price_cents"] + definition["price_offset"])
        acceptance_rate = round(
            clamp(profile["acceptance_rate"] + definition["acceptance_offset"], 55.0, 99.0),
            2,
        )
        zone = slice_zones[index % len(slice_zones)] if slice_zones else None

        provider = Provider.objects.create(
            provider_id=provider_id,
            provider_type="company" if profile["verified"] else "self_employed",
            company_name=f"Seed {definition['city']} Cat{category.id} #{index + 1:02d}",
            contact_first_name="Marketplace",
            contact_last_name=f"Seed{provider_id}",
            phone_number=f"+1-555-{provider_id:04d}",
            email=f"marketplace-seed-{provider_id}@example.com",
            province=definition["province"],
            city=definition["city"],
            zone=zone,
            postal_code=(
                "H1A 0A1"
                if definition["city"] == "Montreal"
                else "M5H 2N2" if definition["city"] == "Toronto" else "H7A 0A1"
            ),
            address_line1="123 Ranking Test Ave",
            avg_rating=rating,
            completed_jobs_count=completed,
            cancelled_jobs_count=cancelled,
            is_verified=profile["verified"],
            acceptance_rate=acceptance_rate,
        )

        ProviderService.objects.create(
            provider=provider,
            category=category,
            custom_name=f"{definition['label']} service",
            description=f"Seeded for marketplace sensitivity analysis ({profile['segment']}).",
            billing_unit="hour",
            price_cents=price_cents,
            is_active=True,
        )
        created += 1

    return created


@transaction.atomic
def main():
    deleted_providers, deleted_objects = delete_existing_seed_data()
    categories = ensure_categories()
    zones_by_city = ensure_zones()
    profiles = build_profiles()

    print(
        "Deleted existing seed data: "
        f"{deleted_providers} providers, {deleted_objects} total objects"
    )

    total_created = 0
    for definition in SLICE_DEFINITIONS:
        created = create_slice(
            definition=definition,
            category=categories[definition["category_id"]],
            profiles=profiles,
            zones_by_city=zones_by_city,
        )
        total_created += created
        print(
            "Created slice "
            f"{definition['label']}: {created} providers "
            f"(category={definition['category_id']}, "
            f"{definition['province']}/{definition['city']})"
        )

    print(f"Total seed providers created: {total_created}")


if __name__ == "__main__":
    main()
