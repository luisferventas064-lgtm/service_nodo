import json
from datetime import timedelta

from django.test import Client as HttpClient
from django.test import TestCase
from django.utils import timezone

from assignments.models import JobAssignment
from clients.models import Client as ClientModel
from jobs.models import Job
from providers.models import Provider
from service_type.models import ServiceType


class ApiServiceLifecycleTests(TestCase):
    def setUp(self):
        self.http = HttpClient()
        self.service_type = ServiceType.objects.create(
            name="API Lifecycle Test",
            description="API Lifecycle Test",
        )
        self.client_owner = ClientModel.objects.create(
            first_name="Client",
            last_name="Owner",
            phone_number="555-111-0001",
            email="client.owner.api@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        self.client_other = ClientModel.objects.create(
            first_name="Client",
            last_name="Other",
            phone_number="555-111-0002",
            email="client.other.api@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="2 Client St",
        )
        self.provider_ok = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Ok",
            phone_number="555-222-0001",
            email="provider.ok.api@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Provider St",
        )
        self.provider_other = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Other",
            phone_number="555-222-0002",
            email="provider.other.api@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="2 Provider St",
        )
        self.job = Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.ASSIGNED,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=2),
            service_type=self.service_type,
            client=self.client_owner,
            selected_provider=self.provider_ok,
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="3 Job St",
        )
        JobAssignment.objects.create(
            job=self.job,
            provider=self.provider_ok,
            is_active=True,
            assignment_status="assigned",
        )

    def _post(self, url: str, payload: dict):
        return self.http.post(url, data=json.dumps(payload), content_type="application/json")

    def test_start_ok(self):
        resp = self._post(f"/api/jobs/{self.job.job_id}/start", {"provider_id": self.provider_ok.provider_id})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["result"], "started")

    def test_start_wrong_provider_403(self):
        resp = self._post(f"/api/jobs/{self.job.job_id}/start", {"provider_id": self.provider_other.provider_id})
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "provider_not_allowed")

    def test_start_idempotent_ok(self):
        first = self._post(f"/api/jobs/{self.job.job_id}/start", {"provider_id": self.provider_ok.provider_id})
        second = self._post(f"/api/jobs/{self.job.job_id}/start", {"provider_id": self.provider_ok.provider_id})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["result"], "already_in_progress")

    def test_close_ok(self):
        self._post(f"/api/jobs/{self.job.job_id}/start", {"provider_id": self.provider_ok.provider_id})
        resp = self._post(f"/api/jobs/{self.job.job_id}/close", {"client_id": self.client_owner.client_id})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["result"], "closed_and_confirmed")

    def test_close_wrong_client_403(self):
        self._post(f"/api/jobs/{self.job.job_id}/start", {"provider_id": self.provider_ok.provider_id})
        resp = self._post(f"/api/jobs/{self.job.job_id}/close", {"client_id": self.client_other.client_id})
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "client_not_allowed")

    def test_close_idempotent_ok(self):
        self._post(f"/api/jobs/{self.job.job_id}/start", {"provider_id": self.provider_ok.provider_id})
        first = self._post(f"/api/jobs/{self.job.job_id}/close", {"client_id": self.client_owner.client_id})
        second = self._post(f"/api/jobs/{self.job.job_id}/close", {"client_id": self.client_owner.client_id})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["result"], "already_confirmed")
