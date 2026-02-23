from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from clients.models import Client, ClientTicket
from jobs.models import Job
from jobs.services import confirm_service_closed_by_client, start_service_by_provider
from jobs.services_normal_client_confirm import confirm_normal_job_by_client
from providers.models import Provider, ProviderTicket
from service_type.models import ServiceType


class ServiceLifecycleTicketingTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Lifecycle Ticketing Test",
            description="Lifecycle Ticketing Test",
        )
        self.client = Client.objects.create(
            first_name="Client",
            last_name="Lifecycle",
            phone_number="555-123-0001",
            email="client.lifecycle@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Lifecycle",
            phone_number="555-123-0002",
            email="provider.lifecycle@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="2 Provider St",
        )
        self.job = Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=3),
            service_type=self.service_type,
            client=self.client,
            selected_provider=self.provider,
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="3 Job St",
        )

    def test_start_and_close_finalize_provider_and_client_tickets(self):
        ok, *_ = confirm_normal_job_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertTrue(ok)

        started = start_service_by_provider(job_id=self.job.job_id, provider_id=self.provider.provider_id)
        self.assertEqual(started, "started")

        closed = confirm_service_closed_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertEqual(closed, "closed_and_confirmed")

        self.job.refresh_from_db()
        self.assertEqual(self.job.job_status, Job.JobStatus.CONFIRMED)

        pt = ProviderTicket.objects.get(provider=self.provider, ref_type="job", ref_id=self.job.job_id)
        ct = ClientTicket.objects.get(client=self.client, ref_type="job", ref_id=self.job.job_id)
        self.assertEqual(pt.stage, ProviderTicket.Stage.FINAL)
        self.assertEqual(pt.status, ProviderTicket.Status.FINALIZED)
        self.assertEqual(ct.stage, ClientTicket.Stage.FINAL)
        self.assertEqual(ct.status, ClientTicket.Status.FINALIZED)

    def test_close_is_idempotent(self):
        ok, *_ = confirm_normal_job_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertTrue(ok)
        start_service_by_provider(job_id=self.job.job_id, provider_id=self.provider.provider_id)
        first = confirm_service_closed_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        second = confirm_service_closed_by_client(job_id=self.job.job_id, client_id=self.client.client_id)

        self.assertEqual(first, "closed_and_confirmed")
        self.assertEqual(second, "already_confirmed")
        self.assertEqual(
            ProviderTicket.objects.filter(provider=self.provider, ref_type="job", ref_id=self.job.job_id).count(),
            1,
        )
        self.assertEqual(
            ClientTicket.objects.filter(client=self.client, ref_type="job", ref_id=self.job.job_id).count(),
            1,
        )
