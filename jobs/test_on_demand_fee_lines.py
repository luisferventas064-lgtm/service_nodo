from django.test import TestCase

from clients.models import Client, ClientTicket
from jobs.models import Job
from jobs.services_normal_client_confirm import confirm_normal_job_by_client
from providers.models import Provider, ProviderTicket
from service_type.models import ServiceType


class OnDemandFeeLinesTests(TestCase):
    def test_confirm_normal_on_demand_creates_fee_lines(self):
        service_type = ServiceType.objects.create(
            name="OnDemand Fee Test",
            description="OnDemand Fee Test",
        )
        client = Client.objects.create(
            first_name="Client",
            last_name="Fee",
            phone_number="555-900-0001",
            email="client.ondemand.fee@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Fee",
            phone_number="555-900-0002",
            email="provider.ondemand.fee@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Provider St",
        )
        job = Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            service_type=service_type,
            client=client,
            selected_provider=provider,
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Job St",
        )

        ok, *_ = confirm_normal_job_by_client(job_id=job.job_id, client_id=client.client_id)
        self.assertTrue(ok)

        pt = ProviderTicket.objects.get(provider=provider, ref_type="job", ref_id=job.job_id)
        ct = ClientTicket.objects.get(client=client, ref_type="job", ref_id=job.job_id)
        self.assertEqual(pt.lines.filter(line_type="fee").count(), 1)
        self.assertEqual(ct.lines.filter(line_type="fee").count(), 1)
