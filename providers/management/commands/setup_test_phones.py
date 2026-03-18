from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from clients.models import Client
from core.utils.phone import TEST_PHONE_PREFIX, generate_test_phone
from providers.models import Provider


class Command(BaseCommand):
    help = "Setup test phone fixtures for onboarding testing in DEBUG mode"

    def add_arguments(self, parser):
        parser.add_argument(
            "--cleanup",
            action="store_true",
            help="Delete all test phone users instead of creating them",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=5,
            help="Number of test fixtures to create (default: 5)",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("This command only works in DEBUG mode.")

        cleanup = options.get("cleanup", False)
        count = options.get("count", 5)

        if cleanup:
            self._cleanup_test_users()
        else:
            self._create_test_fixtures(count)

    def _cleanup_test_users(self):
        client_deleted, _ = Client.objects.filter(phone_number__startswith=TEST_PHONE_PREFIX).delete()
        provider_deleted, _ = Provider.objects.filter(phone_number__startswith=TEST_PHONE_PREFIX).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {client_deleted} test Client(s) and {provider_deleted} test Provider(s)."
            )
        )

    @transaction.atomic
    def _create_test_fixtures(self, count):
        self.stdout.write(self.style.WARNING(f"Setting up {count} test fixtures..."))

        for i in range(1, count + 1):
            client_phone = generate_test_phone(i)
            provider_phone = generate_test_phone(100 + i)

            Client.objects.get_or_create(
                phone_number=client_phone,
                defaults={
                    "first_name": "Test",
                    "last_name": f"Client {i}",
                    "email": f"testclient{i}@localhost.test",
                    "password": "test123456",
                    "country": "Canada",
                    "province": "QC",
                    "city": "Montreal",
                    "postal_code": "H1A 1A1",
                    "address_line1": "123 Test St",
                    "is_phone_verified": True,
                    "accepts_terms": True,
                    "profile_completed": True,
                },
            )

            Provider.objects.get_or_create(
                phone_number=provider_phone,
                defaults={
                    "provider_type": Provider.TYPE_SELF_EMPLOYED,
                    "contact_first_name": "Test",
                    "contact_last_name": f"Provider {i}",
                    "email": f"testprovider{i}@localhost.test",
                    "password": "test123456",
                    "country": "Canada",
                    "province": "QC",
                    "city": "Montreal",
                    "postal_code": "H1A 1A1",
                    "address_line1": "456 Provider Ave",
                    "is_phone_verified": True,
                    "profile_completed": True,
                    "billing_profile_completed": True,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Test fixtures ready. Use phones +11100000001.. for clients, +11100000101.. for providers."
            )
        )
