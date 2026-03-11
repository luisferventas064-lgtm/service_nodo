from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from clients.models import Client
from jobs.management.commands.seed_activity_demo import (
    DEMO_CLIENT_EMAIL,
    DEMO_PROVIDER_EMAIL_PREFIX,
    DEMO_SERVICE_DEFINITIONS,
    DEMO_WORKER_EMAIL,
)
from jobs.models import Job
from providers.models import Provider, ProviderService
from service_type.models import ServiceType
from workers.models import Worker


class SeedActivityDemoCommandTests(TestCase):
    def test_seed_activity_demo_creates_demo_dataset_and_reset_replaces_it(self):
        output = StringIO()

        call_command(
            "seed_activity_demo",
            total=14,
            seed=123,
            reset=True,
            stdout=output,
        )

        client = Client.objects.get(email=DEMO_CLIENT_EMAIL)
        self.assertEqual(Job.objects.filter(client=client).count(), 14)
        self.assertTrue(Provider.objects.filter(email__startswith=DEMO_PROVIDER_EMAIL_PREFIX).exists())
        self.assertTrue(Worker.objects.filter(email=DEMO_WORKER_EMAIL).exists())
        self.assertEqual(
            ServiceType.objects.filter(
                name__in=[definition["name"] for definition in DEMO_SERVICE_DEFINITIONS]
            ).count(),
            len(DEMO_SERVICE_DEFINITIONS),
        )
        self.assertEqual(
            ProviderService.objects.filter(
                provider__email__startswith=DEMO_PROVIDER_EMAIL_PREFIX
            ).count(),
            len(DEMO_SERVICE_DEFINITIONS),
        )
        self.assertTrue(
            Job.objects.filter(
                client=client,
                job_status=Job.JobStatus.CANCELLED,
                cancel_reason=Job.CancelReason.CLIENT_CANCELLED,
            ).exists()
        )
        self.assertTrue(
            Job.objects.filter(
                client=client,
                job_mode=Job.JobMode.SCHEDULED,
            ).exists()
        )
        self.assertTrue(
            Job.objects.filter(
                client=client,
                hold_worker__email=DEMO_WORKER_EMAIL,
            ).exists()
        )
        self.assertIn("Seeded 14 demo activity jobs", output.getvalue())
        self.assertIn("Client:", output.getvalue())
        self.assertIn("Providers:", output.getvalue())
        self.assertIn("Worker:", output.getvalue())

        output = StringIO()
        call_command(
            "seed_activity_demo",
            total=5,
            seed=123,
            reset=True,
            stdout=output,
        )

        self.assertEqual(Job.objects.filter(client=client).count(), 5)
        self.assertIn("Deleted existing demo activity jobs", output.getvalue())
