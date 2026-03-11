from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Client
from jobs.models import Job
from service_type.models import ServiceType
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
        self.assertContains(response, "There was a problem with your submission.")
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
        self.assertContains(jobs_response, 'class="nodo-subnav"')
        self.assertContains(jobs_response, reverse("portal:worker_dashboard"))
        self.assertContains(jobs_response, reverse("worker_jobs"))
        self.assertContains(jobs_response, reverse("worker_activity"))
        self.assertContains(jobs_response, reverse("worker_profile"))
        self.assertContains(jobs_response, reverse("worker_edit"))
        self.assertContains(jobs_response, "Logout")
        self.assertContains(jobs_response, "Assigned Jobs")
        self.assertContains(
            jobs_response,
            f'<a class="nodo-subnav__item active" href="{reverse("worker_jobs")}" aria-current="page">Jobs</a>',
            html=True,
        )

        html = jobs_response.content.decode()
        self.assertLess(html.find('class="nodo-subnav"'), html.find("Assigned Jobs"))

    def test_worker_activity_uses_shared_activity_context(self):
        worker = Worker.objects.create(
            first_name="Active",
            last_name="Worker",
            email="activity.worker@test.local",
            phone_number="+14389216944",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
        )
        client = Client.objects.create(
            first_name="Worker",
            last_name="Client",
            email="worker.client@test.local",
            phone_number="+14389216945",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="6 Client St",
        )
        service_type = ServiceType.objects.create(
            name="Worker Activity Service",
            description="Worker Activity Service",
        )
        matching_job = Job.objects.create(
            client=client,
            hold_worker=worker,
            service_type=service_type,
            provider_service_name_snapshot="Worker Activity Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.ASSIGNED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="7 Job St",
        )
        other_worker = Worker.objects.create(
            first_name="Other",
            last_name="Worker",
            email="other.activity.worker@test.local",
            phone_number="+14389216946",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
        )
        Job.objects.create(
            client=client,
            hold_worker=other_worker,
            service_type=service_type,
            provider_service_name_snapshot="Other Worker Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.IN_PROGRESS,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="8 Job St",
        )

        session = self.client.session
        session["worker_id"] = worker.pk
        session.save()

        response = self.client.get(reverse("worker_activity"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity History")
        self.assertContains(response, matching_job.public_reference)
        self.assertContains(response, "Worker Client")
        self.assertContains(response, "Client")
        self.assertContains(response, "All (1)")
        self.assertContains(response, "Assigned (1)")
        self.assertContains(response, "informational and operational purposes only")
        self.assertNotContains(response, "Payment")
        self.assertNotContains(response, "Gross")
        self.assertNotContains(response, "Earnings")
        self.assertNotContains(response, "Platform fee")
        self.assertNotContains(response, "Other Worker Offer")

    def test_worker_activity_supports_second_page(self):
        worker = Worker.objects.create(
            first_name="Paged",
            last_name="Worker",
            email="paged.activity.worker@test.local",
            phone_number="+14389216947",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
        )
        client = Client.objects.create(
            first_name="Paged",
            last_name="Client",
            email="paged.worker.client@test.local",
            phone_number="+14389216948",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="9 Client St",
        )
        service_type = ServiceType.objects.create(
            name="Paged Worker Activity Service",
            description="Paged Worker Activity Service",
        )
        for index in range(11):
            Job.objects.create(
                client=client,
                hold_worker=worker,
                service_type=service_type,
                provider_service_name_snapshot=f"Worker Page Offer {index}",
                job_mode=Job.JobMode.ON_DEMAND,
                job_status=Job.JobStatus.ASSIGNED,
                is_asap=True,
                country="Canada",
                province="QC",
                city="Montreal",
                postal_code="H1A1A1",
                address_line1="10 Job St",
            )

        session = self.client.session
        session["worker_id"] = worker.pk
        session.save()

        response = self.client.get(reverse("worker_activity"), {"page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertTrue(response.context["is_paginated"])
        self.assertContains(response, "Page 2 of 2")

    def test_worker_activity_supports_date_range_filter(self):
        worker = Worker.objects.create(
            first_name="Range",
            last_name="Worker",
            email="range.activity.worker@test.local",
            phone_number="+14389216949",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
        )
        client = Client.objects.create(
            first_name="Range",
            last_name="Client",
            email="range.worker.client@test.local",
            phone_number="+14389216950",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="11 Client St",
        )
        service_type = ServiceType.objects.create(
            name="Range Worker Activity Service",
            description="Range Worker Activity Service",
        )
        recent_job = Job.objects.create(
            client=client,
            hold_worker=worker,
            service_type=service_type,
            provider_service_name_snapshot="Recent Worker Range Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.ASSIGNED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="12 Job St",
        )
        old_job = Job.objects.create(
            client=client,
            hold_worker=worker,
            service_type=service_type,
            provider_service_name_snapshot="Old Worker Range Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.ASSIGNED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A2",
            address_line1="13 Job St",
        )
        Job.objects.filter(pk=recent_job.pk).update(created_at=timezone.now() - timedelta(days=2))
        Job.objects.filter(pk=old_job.pk).update(created_at=timezone.now() - timedelta(days=8))

        session = self.client.session
        session["worker_id"] = worker.pk
        session.save()

        response = self.client.get(reverse("worker_activity"), {"range": "7d"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_range"], "7d")
        self.assertContains(response, "Recent Worker Range Offer")
        self.assertNotContains(response, "Old Worker Range Offer")

    def test_worker_activity_exports_csv(self):
        worker = Worker.objects.create(
            first_name="Export",
            last_name="Worker",
            email="export.activity.worker@test.local",
            phone_number="+14389216951",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
        )
        client = Client.objects.create(
            first_name="Export",
            last_name="Client",
            email="export.worker.client@test.local",
            phone_number="+14389216952",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="14 Client St",
        )
        service_type = ServiceType.objects.create(
            name="Export Worker Activity Service",
            description="Export Worker Activity Service",
        )
        job = Job.objects.create(
            client=client,
            hold_worker=worker,
            service_type=service_type,
            provider_service_name_snapshot="Worker Export Offer",
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.ASSIGNED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="15 Job St",
        )

        session = self.client.session
        session["worker_id"] = worker.pk
        session.save()

        response = self.client.get(reverse("worker_activity"), {"export": "csv"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode("utf-8")
        self.assertIn(
            "Job ID,Date,Service,Provider,Status,Cancelled Reason",
            content,
        )
        self.assertIn(str(job.job_id), content)
        self.assertNotIn("Total charged", content)
        self.assertNotIn("Gross", content)
        self.assertIn("informational and operational purposes only", content)
        self.assertNotIn("Export Client", content)
