from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from ui.models import PasswordResetCode

from .models import Worker


class WorkerViewsTests(TestCase):
    @patch("workers.views.send_sms")
    def test_worker_register_creates_unverified_worker_and_redirects_to_verify(self, send_sms_mock):
        response = self.client.post(
            reverse("worker_register"),
            data={
                "full_name": "Jane Worker",
                "email": "jane.worker@example.com",
                "country": "CA",
                "phone_local": "4389216948",
                "password": "Worker123!",
                "confirm_password": "Worker123!",
            },
        )

        self.assertRedirects(response, reverse("verify_phone"))
        worker = Worker.objects.get(email="jane.worker@example.com")
        self.assertEqual(worker.phone_number, "+14389216948")
        self.assertFalse(worker.is_phone_verified)
        self.assertEqual(self.client.session["verify_phone"], worker.phone_number)
        self.assertEqual(self.client.session["verify_role"], "worker")
        self.assertTrue(
            PasswordResetCode.objects.filter(
                phone_number=worker.phone_number,
                purpose="verify",
                used=False,
            ).exists()
        )
        send_sms_mock.assert_called_once()

    def test_worker_register_rejects_password_mismatch(self):
        response = self.client.post(
            reverse("worker_register"),
            data={
                "full_name": "Jane Worker",
                "email": "jane.worker.mismatch@example.com",
                "country": "CA",
                "phone_local": "4389216950",
                "password": "Worker123!",
                "confirm_password": "Worker123?wrong",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Passwords do not match.")
        self.assertFalse(Worker.objects.filter(email="jane.worker.mismatch@example.com").exists())

    def test_worker_profile_redirects_to_login_without_session(self):
        response = self.client.get(reverse("worker_profile"))

        self.assertRedirects(response, reverse("ui:login"))

    def test_worker_jobs_redirects_to_login_without_session(self):
        response = self.client.get(reverse("worker_jobs"))

        self.assertRedirects(response, reverse("ui:login"))

    def test_worker_profile_renders_completion_form_for_incomplete_worker(self):
        worker = Worker.objects.create(
            first_name="Pending",
            last_name="Worker",
            email="pending.worker@test.local",
            phone_number="+14389216940",
            is_phone_verified=True,
            profile_completed=False,
            accepts_terms=False,
        )
        session = self.client.session
        session["worker_id"] = worker.pk
        session.save()

        response = self.client.get(reverse("worker_profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Complete Worker Profile")

    def test_worker_profile_post_marks_worker_complete_and_redirects_jobs(self):
        worker = Worker.objects.create(
            first_name="Pending",
            last_name="Worker",
            email="post.worker@test.local",
            phone_number="+14389216941",
            is_phone_verified=True,
            profile_completed=False,
            accepts_terms=False,
        )
        session = self.client.session
        session["worker_id"] = worker.pk
        session.save()

        response = self.client.post(
            reverse("worker_profile"),
            data={
                "first_name": "Ready",
                "last_name": "Worker",
                "accepts_terms": "on",
            },
        )

        self.assertRedirects(response, reverse("worker_jobs"))
        worker.refresh_from_db()
        self.assertEqual(worker.first_name, "Ready")
        self.assertTrue(worker.accepts_terms)
        self.assertTrue(worker.profile_completed)

    def test_worker_jobs_redirects_incomplete_worker_to_profile(self):
        worker = Worker.objects.create(
            first_name="Pending",
            last_name="Worker",
            email="jobs.pending.worker@test.local",
            phone_number="+14389216942",
            is_phone_verified=True,
            profile_completed=False,
            accepts_terms=False,
        )
        session = self.client.session
        session["worker_id"] = worker.pk
        session.save()

        response = self.client.get(reverse("worker_jobs"))

        self.assertRedirects(response, reverse("worker_profile"))

    def test_worker_jobs_render_for_complete_worker(self):
        worker = Worker.objects.create(
            first_name="Ready",
            last_name="Worker",
            email="jobs.ready.worker@test.local",
            phone_number="+14389216943",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
        )
        session = self.client.session
        session["worker_id"] = worker.pk
        session.save()

        profile_response = self.client.get(reverse("worker_profile"))
        jobs_response = self.client.get(reverse("worker_jobs"))

        self.assertRedirects(profile_response, reverse("worker_jobs"))
        self.assertEqual(jobs_response.status_code, 200)
        self.assertContains(jobs_response, "Assigned Jobs")
