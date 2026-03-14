from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from clients.models import Client
from jobs.events import create_job_event
from jobs.models import Job, JobEvent
from notifications.models import PushDevice, PushDispatchAttempt
from providers.models import Provider, ProviderUser
from service_type.models import ServiceType


class DispatchJobEventPushTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Push Dispatch Test",
            description="Push Dispatch Test",
        )

    def _create_job(self):
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Dispatch",
            contact_last_name="Provider",
            phone_number="5558000001",
            email="provider.push.dispatch@test.local",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="100 Provider St",
        )
        client = Client.objects.create(
            first_name="Dispatch",
            last_name="Client",
            phone_number="5558000002",
            email="client.push.dispatch@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="101 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        return Job.objects.create(
            selected_provider=provider,
            client=client,
            service_type=self.service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="102 Job St",
        )

    def test_create_job_event_dispatches_waiting_provider_response_to_provider_devices(self):
        job = self._create_job()
        user_model = get_user_model()
        provider_user = user_model.objects.create_user(
            username="provider_push_dispatch",
            email="provider.push.dispatch@test.local",
            password="test-pass-123",
        )
        ProviderUser.objects.create(
            provider=job.selected_provider,
            user=provider_user,
            role="owner",
            is_active=True,
        )
        device = PushDevice.objects.create(
            user=provider_user,
            role=PushDevice.Role.PROVIDER,
            platform=PushDevice.Platform.IOS,
            token="provider-device-token",
        )

        with self.captureOnCommitCallbacks(execute=True):
            job_event = create_job_event(
                job=job,
                event_type=JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
                actor_role=JobEvent.ActorRole.SYSTEM,
                provider_id=job.selected_provider_id,
                unique_per_job=True,
            )

        attempt = PushDispatchAttempt.objects.get(job_event=job_event, device=device)
        self.assertEqual(attempt.status, PushDispatchAttempt.Status.STUB_SENT)
        self.assertEqual(
            attempt.payload_json,
            {
                "event_type": JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
                "job_id": str(job.job_id),
                "visible_status": "Waiting for provider response",
            },
        )
        self.assertEqual(attempt.response_json["token"], "provider-device-token")

    def test_create_job_event_dispatches_job_completed_to_client_devices(self):
        job = self._create_job()
        user_model = get_user_model()
        client_user = user_model.objects.create_user(
            username="client_push_dispatch",
            email="client.push.dispatch@test.local",
            password="test-pass-123",
        )
        device = PushDevice.objects.create(
            user=client_user,
            role=PushDevice.Role.CLIENT,
            platform=PushDevice.Platform.ANDROID,
            token="client-device-token",
        )

        with self.captureOnCommitCallbacks(execute=True):
            job_event = create_job_event(
                job=job,
                event_type=JobEvent.EventType.JOB_COMPLETED,
                actor_role=JobEvent.ActorRole.PROVIDER,
                provider_id=job.selected_provider_id,
                unique_per_job=True,
                job_status=Job.JobStatus.COMPLETED,
            )

        attempt = PushDispatchAttempt.objects.get(job_event=job_event, device=device)
        self.assertEqual(attempt.status, PushDispatchAttempt.Status.STUB_SENT)
        self.assertEqual(attempt.payload_json["event_type"], JobEvent.EventType.JOB_COMPLETED)
        self.assertEqual(attempt.payload_json["visible_status"], "Completed")

    @override_settings(PUSH_PROVIDER="fcm")
    @patch("notifications.services.send_fcm_push")
    def test_create_job_event_dispatches_via_fcm_provider_when_configured(
        self,
        send_fcm_push_mock,
    ):
        job = self._create_job()
        user_model = get_user_model()
        client_user = user_model.objects.create_user(
            username="client_push_dispatch_fcm",
            email="client.push.dispatch@test.local",
            password="test-pass-123",
        )
        device = PushDevice.objects.create(
            user=client_user,
            role=PushDevice.Role.CLIENT,
            platform=PushDevice.Platform.ANDROID,
            token="client-device-token-fcm",
        )
        send_fcm_push_mock.return_value = {
            "ok": True,
            "provider": "fcm",
            "token": device.token,
            "status_code": 200,
            "provider_message_id": "projects/demo/messages/123",
            "response_json": {"name": "projects/demo/messages/123"},
        }

        with self.captureOnCommitCallbacks(execute=True):
            job_event = create_job_event(
                job=job,
                event_type=JobEvent.EventType.JOB_COMPLETED,
                actor_role=JobEvent.ActorRole.PROVIDER,
                provider_id=job.selected_provider_id,
                unique_per_job=True,
                job_status=Job.JobStatus.COMPLETED,
            )

        attempt = PushDispatchAttempt.objects.get(job_event=job_event, device=device)
        self.assertEqual(attempt.status, PushDispatchAttempt.Status.SENT)
        self.assertEqual(attempt.response_json["provider"], "fcm")
        send_fcm_push_mock.assert_called_once_with(
            token=device.token,
            payload={
                "event_type": JobEvent.EventType.JOB_COMPLETED,
                "job_id": str(job.job_id),
                "visible_status": "Completed",
            },
        )
