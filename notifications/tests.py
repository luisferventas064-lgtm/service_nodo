from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from clients.models import Client
from jobs.events import create_job_event
from jobs.models import Job, JobEvent, JobProviderExclusion
from notifications.models import PushDevice, PushDispatchAttempt
from providers.models import Provider, ProviderUser
from service_type.models import ServiceType


@override_settings(PUSH_PROVIDER="stub")
class DispatchJobEventPushTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Push Dispatch Test",
            description="Push Dispatch Test",
        )
        self.user_model = get_user_model()
        self.job = self._create_job()
        self.client_user = self.user_model.objects.create_user(
            username="client_push_dispatch",
            email=self.job.client.email,
            password="test-pass-123",
        )
        self.provider_user = self.user_model.objects.create_user(
            username="provider_push_dispatch",
            email=self.job.selected_provider.email,
            password="test-pass-123",
        )
        ProviderUser.objects.create(
            provider=self.job.selected_provider,
            user=self.provider_user,
            role="owner",
            is_active=True,
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

    def _dispatch_job_event(self, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return create_job_event(job=self.job, **kwargs)

    def _assert_stub_attempt(self, *, job_event, device, expected_payload):
        attempts = PushDispatchAttempt.objects.filter(job_event=job_event)
        self.assertEqual(attempts.count(), 1)
        attempt = attempts.get(device=device)
        self.assertEqual(attempt.status, PushDispatchAttempt.Status.STUB_SENT)
        self.assertEqual(attempt.device.token, device.token)
        self.assertEqual(attempt.payload_json, expected_payload)
        self.assertEqual(attempt.response_json["provider"], "stub")
        self.assertEqual(attempt.response_json["token"], device.token)
        return attempt

    def test_job_created_dispatches_stub_push_to_client_device(self):
        device = PushDevice.objects.create(
            user=self.client_user,
            role=PushDevice.Role.CLIENT,
            platform=PushDevice.Platform.ANDROID,
            token="test-client-token",
            is_active=True,
        )

        job_event = self._dispatch_job_event(
            event_type=JobEvent.EventType.JOB_CREATED,
            actor_role=JobEvent.ActorRole.CLIENT,
            payload={"source": "test"},
        )

        self._assert_stub_attempt(
            job_event=job_event,
            device=device,
            expected_payload={
                "event_type": JobEvent.EventType.JOB_CREATED,
                "job_id": str(self.job.job_id),
                "visible_status": "Waiting for provider response",
            },
        )

    def test_waiting_provider_response_dispatches_stub_push_to_provider_device(self):
        device = PushDevice.objects.create(
            user=self.provider_user,
            role=PushDevice.Role.PROVIDER,
            platform=PushDevice.Platform.IOS,
            token="provider-device-token",
            is_active=True,
        )

        job_event = self._dispatch_job_event(
            event_type=JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
            actor_role=JobEvent.ActorRole.SYSTEM,
            provider_id=self.job.selected_provider_id,
            unique_per_job=True,
        )

        self._assert_stub_attempt(
            job_event=job_event,
            device=device,
            expected_payload={
                "event_type": JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
                "job_id": str(self.job.job_id),
                "visible_status": "Waiting for provider response",
            },
        )

    def test_waiting_provider_response_skips_provider_device_after_decline_exclusion(self):
        PushDevice.objects.create(
            user=self.provider_user,
            role=PushDevice.Role.PROVIDER,
            platform=PushDevice.Platform.IOS,
            token="provider-device-token-excluded",
            is_active=True,
        )
        JobProviderExclusion.objects.create(
            job=self.job,
            provider=self.job.selected_provider,
            reason=JobProviderExclusion.Reason.DECLINED,
        )

        job_event = self._dispatch_job_event(
            event_type=JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
            actor_role=JobEvent.ActorRole.SYSTEM,
            provider_id=self.job.selected_provider_id,
        )

        self.assertEqual(PushDispatchAttempt.objects.filter(job_event=job_event).count(), 0)

    def test_job_completed_dispatches_stub_push_to_client_device(self):
        device = PushDevice.objects.create(
            user=self.client_user,
            role=PushDevice.Role.CLIENT,
            platform=PushDevice.Platform.ANDROID,
            token="client-device-token",
            is_active=True,
        )

        job_event = self._dispatch_job_event(
            event_type=JobEvent.EventType.JOB_COMPLETED,
            actor_role=JobEvent.ActorRole.PROVIDER,
            provider_id=self.job.selected_provider_id,
            unique_per_job=True,
            job_status=Job.JobStatus.COMPLETED,
        )

        self._assert_stub_attempt(
            job_event=job_event,
            device=device,
            expected_payload={
                "event_type": JobEvent.EventType.JOB_COMPLETED,
                "job_id": str(self.job.job_id),
                "visible_status": "Completed",
            },
        )

    @override_settings(PUSH_PROVIDER="fcm")
    @patch("notifications.services.send_fcm_push")
    def test_create_job_event_dispatches_via_fcm_provider_when_configured(
        self,
        send_fcm_push_mock,
    ):
        device = PushDevice.objects.create(
            user=self.client_user,
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

        job_event = self._dispatch_job_event(
            event_type=JobEvent.EventType.JOB_COMPLETED,
            actor_role=JobEvent.ActorRole.PROVIDER,
            provider_id=self.job.selected_provider_id,
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
                "job_id": str(self.job.job_id),
                "visible_status": "Completed",
            },
        )
