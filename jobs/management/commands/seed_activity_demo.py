from datetime import time, timedelta
from decimal import Decimal
import random

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from clients.models import Client
from jobs.models import Job
from providers.models import Provider, ProviderService
from service_type.models import ServiceType
from workers.models import Worker


DEMO_CLIENT_EMAIL = "activity-demo-client@example.com"
DEMO_PROVIDER_EMAIL_PREFIX = "activity-demo-provider-"
DEMO_WORKER_EMAIL = "activity-demo-worker@example.com"
DEMO_SERVICE_DEFINITIONS = (
    {
        "name": "Seed Activity Cleaning",
        "description": "Demo cleaning service for Activity UI validation.",
        "offer_name": "Cleaning Visit",
        "billing_unit": "fixed",
        "price_cents": 12500,
    },
    {
        "name": "Seed Activity Handyman",
        "description": "Demo handyman service for Activity UI validation.",
        "offer_name": "Handyman Visit",
        "billing_unit": "hour",
        "price_cents": 9000,
    },
    {
        "name": "Seed Activity Moving",
        "description": "Demo moving service for Activity UI validation.",
        "offer_name": "Moving Support",
        "billing_unit": "hour",
        "price_cents": 11000,
    },
)
DEMO_PROVIDER_DEFINITIONS = (
    {
        "suffix": "montreal",
        "first_name": "Mila",
        "last_name": "Montreal",
        "company_name": "Activity Demo Montreal",
        "city": "Montreal",
        "province": "QC",
        "postal_code": "H2X1A1",
        "address_line1": "101 Demo Ave",
        "phone_number": "+15145557101",
    },
    {
        "suffix": "laval",
        "first_name": "Leo",
        "last_name": "Laval",
        "company_name": "Activity Demo Laval",
        "city": "Laval",
        "province": "QC",
        "postal_code": "H7A1A1",
        "address_line1": "202 Demo Blvd",
        "phone_number": "+15145557102",
    },
    {
        "suffix": "longueuil",
        "first_name": "Lina",
        "last_name": "Longueuil",
        "company_name": "Activity Demo Longueuil",
        "city": "Longueuil",
        "province": "QC",
        "postal_code": "J4K1A1",
        "address_line1": "303 Demo Rd",
        "phone_number": "+15145557103",
    },
)
DEMO_STATUS_SEQUENCE = (
    Job.JobStatus.POSTED,
    Job.JobStatus.WAITING_PROVIDER_RESPONSE,
    Job.JobStatus.ASSIGNED,
    Job.JobStatus.IN_PROGRESS,
    Job.JobStatus.COMPLETED,
    Job.JobStatus.CONFIRMED,
    Job.JobStatus.CANCELLED,
)


def _demo_client():
    client, created = Client.objects.get_or_create(
        email=DEMO_CLIENT_EMAIL,
        defaults={
            "first_name": "Activity",
            "last_name": "Demo Client",
            "phone_number": "+15145557001",
            "is_phone_verified": True,
            "accepts_terms": True,
            "profile_completed": True,
            "country": "Canada",
            "province": "QC",
            "city": "Montreal",
            "postal_code": "H2X1A1",
            "address_line1": "1 Demo Client St",
        },
    )
    if created:
        return client

    updated_fields = []
    if not client.accepts_terms:
        client.accepts_terms = True
        updated_fields.append("accepts_terms")
    if not client.profile_completed:
        client.profile_completed = True
        updated_fields.append("profile_completed")
    if updated_fields:
        updated_fields.append("updated_at")
        client.save(update_fields=updated_fields)
    return client


def _demo_worker():
    worker, created = Worker.objects.get_or_create(
        email=DEMO_WORKER_EMAIL,
        defaults={
            "first_name": "Activity",
            "last_name": "Demo Worker",
            "phone_number": "+15145557002",
            "is_phone_verified": True,
            "accepts_terms": True,
            "profile_completed": True,
            "country": "Canada",
            "province": "QC",
            "city": "Montreal",
            "postal_code": "H2X1A2",
            "address_line1": "2 Demo Worker St",
        },
    )
    if created:
        return worker

    updated_fields = []
    if not worker.accepts_terms:
        worker.accepts_terms = True
        updated_fields.append("accepts_terms")
    if not worker.profile_completed:
        worker.profile_completed = True
        updated_fields.append("profile_completed")
    if updated_fields:
        updated_fields.append("updated_at")
        worker.save(update_fields=updated_fields)
    return worker


def _demo_service_types():
    service_types = []
    for definition in DEMO_SERVICE_DEFINITIONS:
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
    return service_types


def _demo_providers():
    providers = []
    for index, definition in enumerate(DEMO_PROVIDER_DEFINITIONS, start=1):
        provider, _ = Provider.objects.get_or_create(
            email=f"{DEMO_PROVIDER_EMAIL_PREFIX}{definition['suffix']}@example.com",
            defaults={
                "provider_type": Provider.TYPE_COMPANY,
                "company_name": definition["company_name"],
                "legal_name": definition["company_name"],
                "contact_first_name": definition["first_name"],
                "contact_last_name": definition["last_name"],
                "phone_number": definition["phone_number"],
                "is_phone_verified": True,
                "profile_completed": True,
                "billing_profile_completed": True,
                "accepts_terms": True,
                "country": "Canada",
                "province": definition["province"],
                "city": definition["city"],
                "postal_code": definition["postal_code"],
                "address_line1": definition["address_line1"],
                "service_area": definition["city"],
                "is_active": True,
            },
        )
        providers.append(provider)
    return providers


def _demo_provider_services(providers, service_types):
    provider_services = []
    for index, provider in enumerate(providers):
        definition = DEMO_SERVICE_DEFINITIONS[index % len(DEMO_SERVICE_DEFINITIONS)]
        service_type = service_types[index % len(service_types)]
        provider_service, _ = ProviderService.objects.get_or_create(
            provider=provider,
            service_type=service_type,
            custom_name=f"{definition['offer_name']} {index + 1}",
            defaults={
                "description": definition["description"],
                "billing_unit": definition["billing_unit"],
                "price_cents": definition["price_cents"],
                "is_active": True,
            },
        )
        if not provider_service.is_active:
            provider_service.is_active = True
            provider_service.save(update_fields=["is_active"])
        provider_services.append(provider_service)
    return provider_services


class Command(BaseCommand):
    help = "Seed demo Activity jobs for client, provider, and worker browser testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--total",
            type=int,
            default=30,
            help="Number of demo jobs to create. Defaults to 30.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=20260310,
            help="Random seed for deterministic demo data. Defaults to 20260310.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing demo activity jobs before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        total = max(0, int(options["total"]))
        rng = random.Random(int(options["seed"]))

        client = _demo_client()
        worker = _demo_worker()
        service_types = _demo_service_types()
        providers = _demo_providers()
        provider_services = _demo_provider_services(providers, service_types)

        if options["reset"]:
            deleted_count, _ = Job.objects.filter(client=client).delete()
            self.stdout.write(f"Deleted existing demo activity jobs: {deleted_count}")

        jobs = []
        now = timezone.now()
        today = timezone.localdate()

        for index in range(total):
            status = DEMO_STATUS_SEQUENCE[index % len(DEMO_STATUS_SEQUENCE)]
            provider_service = provider_services[index % len(provider_services)]
            provider = provider_service.provider
            service_type = provider_service.service_type
            created_at = now - timedelta(
                days=rng.randint(0, 45),
                minutes=index * 11,
            )
            updated_at = created_at + timedelta(minutes=rng.randint(5, 180))
            is_scheduled = index % 3 == 0
            scheduled_date = None
            scheduled_start_time = None
            if is_scheduled:
                scheduled_date = today + timedelta(days=(index % 14) + 1)
                scheduled_start_time = time(
                    hour=9 + (index % 8),
                    minute=30 if index % 2 else 0,
                )

            has_selected_provider = not (
                status == Job.JobStatus.POSTED and index % 5 == 0
            )
            selected_provider = provider if has_selected_provider else None
            selected_provider_service = provider_service if has_selected_provider else None
            provider_service_name_snapshot = (
                provider_service.custom_name
                if selected_provider_service
                else f"{service_type.name} Request"
            )

            amount = Decimal(provider_service.price_cents) / Decimal("100")
            quantity = Decimal((index % 3) + 1)
            line_total = amount * quantity
            hold_worker = (
                worker
                if status
                in {
                    Job.JobStatus.ASSIGNED,
                    Job.JobStatus.IN_PROGRESS,
                    Job.JobStatus.COMPLETED,
                    Job.JobStatus.CONFIRMED,
                }
                and index % 2 == 0
                else None
            )

            job_kwargs = {
                "client": client,
                "service_type": service_type,
                "country": "Canada",
                "province": provider.province,
                "city": provider.city,
                "postal_code": provider.postal_code,
                "address_line1": f"{400 + index} Activity Demo Lane",
                "job_mode": (
                    Job.JobMode.SCHEDULED
                    if is_scheduled
                    else Job.JobMode.ON_DEMAND
                ),
                "job_status": status,
                "is_asap": not is_scheduled,
                "scheduled_date": scheduled_date,
                "scheduled_start_time": scheduled_start_time,
                "selected_provider": selected_provider,
                "provider_service": selected_provider_service,
                "provider_service_name_snapshot": provider_service_name_snapshot,
                "hold_worker": hold_worker,
                "created_at": created_at,
                "updated_at": updated_at,
            }

            if status == Job.JobStatus.CANCELLED:
                job_kwargs["cancelled_by"] = Job.CancellationActor.CLIENT
                job_kwargs["cancel_reason"] = Job.CancelReason.CLIENT_CANCELLED

            if index % 2 == 0:
                job_kwargs["requested_quantity_snapshot"] = quantity
                job_kwargs["requested_unit_price_snapshot"] = amount
                job_kwargs["requested_base_line_total_snapshot"] = line_total
                job_kwargs["requested_subtotal_snapshot"] = line_total
                job_kwargs["requested_total_snapshot"] = line_total
                job_kwargs["requested_billing_unit_snapshot"] = provider_service.billing_unit

            jobs.append(Job(**job_kwargs))

        Job.objects.bulk_create(jobs)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {total} demo activity jobs for {client.email}."
            )
        )
        self.stdout.write(f"Client: {client.client_id} / {client.email}")
        self.stdout.write(
            "Providers: "
            + ", ".join(
                f"{provider.provider_id} / {provider.email}"
                for provider in providers
            )
        )
        self.stdout.write(f"Worker: {worker.worker_id} / {worker.email}")
