from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from clients.models import Client
from jobs.models import Job
from providers.models import Provider, ProviderService, ProviderServiceArea, ProviderTicket
from service_type.models import ServiceType
from workers.models import Worker


class PortalRoutingTests(TestCase):
    def test_client_dashboard_renders_shared_subnav_before_content(self):
        client_obj = Client.objects.create(
            first_name="Portal",
            last_name="Client",
            phone_number="5550001024",
            email="portal.dashboard.client.render@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1024 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("client_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="nodo-subnav"')
        self.assertContains(response, reverse("client_dashboard"))
        self.assertContains(response, reverse("ui:marketplace_search"))
        self.assertContains(response, reverse("client_activity"))
        self.assertContains(response, reverse("client_profile"))
        self.assertContains(response, reverse("client_billing"))
        self.assertContains(response, "Logout")
        self.assertContains(response, "Portal Client \u2013 Client")
        self.assertNotContains(response, ">Account<", html=False)
        self.assertContains(
            response,
            f'<a class="nodo-subnav__item active" href="{reverse("client_dashboard")}" aria-current="page">Dashboard</a>',
            html=True,
        )

        html = response.content.decode()
        self.assertLess(html.find('class="nodo-subnav"'), html.find("Client Dashboard"))

    def test_client_dashboard_shows_only_active_recent_jobs(self):
        client_obj = Client.objects.create(
            first_name="Portal",
            last_name="Client Jobs",
            phone_number="5550001025",
            email="portal.dashboard.client.jobs@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1025 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        waiting_service = ServiceType.objects.create(
            name="Waiting Service",
            description="Waiting Service",
        )
        confirmed_service = ServiceType.objects.create(
            name="Confirmed Service",
            description="Confirmed Service",
        )
        completed_service = ServiceType.objects.create(
            name="Completed Service",
            description="Completed Service",
        )
        cancelled_service = ServiceType.objects.create(
            name="Cancelled Service",
            description="Cancelled Service",
        )

        def make_job(*, service_type, status):
            job_kwargs = {
                "client": client_obj,
                "service_type": service_type,
                "job_mode": Job.JobMode.ON_DEMAND,
                "job_status": status,
                "is_asap": True,
                "country": "Canada",
                "province": "QC",
                "city": "Montreal",
                "postal_code": "H1A1A1",
                "address_line1": "1025 Client St",
            }
            if status == Job.JobStatus.CANCELLED:
                job_kwargs["cancelled_by"] = Job.CancellationActor.CLIENT
                job_kwargs["cancel_reason"] = Job.CancelReason.CLIENT_CANCELLED

            return Job.objects.create(
                **job_kwargs,
            )

        make_job(
            service_type=waiting_service,
            status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
        )
        make_job(
            service_type=confirmed_service,
            status=Job.JobStatus.CONFIRMED,
        )
        make_job(
            service_type=completed_service,
            status=Job.JobStatus.COMPLETED,
        )
        make_job(
            service_type=cancelled_service,
            status=Job.JobStatus.CANCELLED,
        )

        response = self.client.get(reverse("client_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recent Active Jobs")
        self.assertContains(response, "Waiting Service")
        self.assertContains(response, "Confirmed Service")
        self.assertNotContains(response, "Completed Service")
        self.assertNotContains(response, "Cancelled Service")

    def test_client_dashboard_alias_redirects_to_client_dashboard(self):
        client_obj = Client.objects.create(
            first_name="Portal",
            last_name="Client",
            phone_number="5550001020",
            email="portal.dashboard.client@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1020 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )
        session = self.client.session
        session["client_id"] = client_obj.pk
        session.save()

        response = self.client.get(reverse("portal:client_dashboard"))

        self.assertRedirects(response, reverse("client_dashboard"))

    def test_client_dashboard_alias_redirects_to_login_for_non_client(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            contact_first_name="Portal",
            contact_last_name="Provider",
            phone_number="5550001021",
            email="portal.dashboard.provider@test.local",
            profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1021 Provider St",
        )
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("portal:client_dashboard"))

        self.assertRedirects(
            response,
            reverse("ui:root_login"),
            fetch_redirect_response=False,
        )

    def test_provider_dashboard_renders_portal_dashboard(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            contact_first_name="Portal",
            contact_last_name="Provider",
            phone_number="5550001022",
            email="portal.dashboard.provider2@test.local",
            profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="1022 Provider St",
        )
        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("portal:provider_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="nodo-subnav"')
        self.assertContains(response, reverse("portal:provider_dashboard"))
        self.assertContains(response, reverse("portal:provider_services"))
        self.assertContains(response, reverse("provider_profile"))
        self.assertContains(response, reverse("provider_jobs"))
        self.assertContains(response, reverse("provider_activity"))
        self.assertContains(response, reverse("provider_financial_summary"))
        self.assertContains(response, reverse("provider_compliance"))
        self.assertContains(response, reverse("provider_billing"))
        self.assertContains(response, "Logout")
        self.assertContains(response, "Provider Dashboard")
        self.assertContains(
            response,
            f'<a class="nodo-subnav__item active" href="{reverse("portal:provider_dashboard")}" aria-current="page">Dashboard</a>',
            html=True,
        )

        html = response.content.decode()
        self.assertLess(html.find('class="nodo-subnav"'), html.find("Provider Dashboard"))

    def test_provider_dashboard_uses_finalized_totals_over_quoted_amounts(self):
        provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            contact_first_name="Revenue",
            contact_last_name="Provider",
            phone_number="5550002022",
            email="portal.dashboard.provider.revenue@test.local",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
            billing_profile_completed=True,
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="2022 Provider St",
        )
        client_obj = Client.objects.create(
            first_name="Revenue",
            last_name="Client",
            phone_number="5550002023",
            email="portal.dashboard.client.revenue@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="2023 Client St",
            is_phone_verified=True,
            profile_completed=True,
            accepts_terms=True,
        )
        service_type = ServiceType.objects.create(
            name="Dashboard Revenue Service",
            description="Dashboard Revenue Service",
        )
        job = Job.objects.create(
            client=client_obj,
            selected_provider=provider,
            service_type=service_type,
            provider_service_name_snapshot="Revenue Offer",
            requested_total_snapshot=Decimal("120.00"),
            quoted_total_price_cents=12000,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.COMPLETED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="2024 Job St",
        )
        ProviderTicket.objects.create(
            provider=provider,
            ref_type="job",
            ref_id=job.job_id,
            ticket_no="PT-2024-0001",
            subtotal_cents=14500,
            tax_cents=2171,
            total_cents=16671,
            currency="CAD",
            tax_region_code="CA-QC",
        )

        session = self.client.session
        session["provider_id"] = provider.pk
        session.save()

        response = self.client.get(reverse("portal:provider_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["revenue_amount"], Decimal("166.71"))
        self.assertContains(response, "166,71 $")
        self.assertContains(response, "Base sur les totaux finalises lorsqu'ils sont disponibles")
        self.assertNotContains(response, "Based on quoted totals")

    def test_worker_dashboard_alias_redirects_to_worker_jobs(self):
        worker = Worker.objects.create(
            first_name="Portal",
            last_name="Worker",
            phone_number="5550001023",
            email="portal.dashboard.worker@test.local",
            profile_completed=True,
            is_phone_verified=True,
            accepts_terms=True,
        )
        session = self.client.session
        session["worker_id"] = worker.pk
        session.save()

        response = self.client.get(reverse("portal:worker_dashboard"))

        self.assertRedirects(response, reverse("worker_jobs"))


class ProviderServicesPortalTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            legal_name="Portal Services Provider",
            contact_first_name="Portal",
            contact_last_name="Services",
            phone_number="+15145550999",
            email="portal.services.provider@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area="Montreal",
            province="QC",
            city="Montreal",
            postal_code="H1A1A1",
            address_line1="999 Provider St",
        )
        ProviderServiceArea.objects.create(
            provider=self.provider,
            city="Montreal",
            province="QC",
            is_active=True,
        )
        self.service_type = ServiceType.objects.create(
            name="Window Cleaning",
            description="Window cleaning",
            is_active=True,
        )
        session = self.client.session
        session["provider_id"] = self.provider.pk
        session.save()

    def test_provider_services_view_renders(self):
        response = self.client.get(reverse("portal:provider_services"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Services")
        self.assertContains(
            response,
            f'<a class="nodo-subnav__item active" href="{reverse("portal:provider_services")}" aria-current="page">My Services</a>',
            html=True,
        )

    def test_provider_services_view_redirects_when_profile_incomplete(self):
        self.provider.legal_name = ""
        self.provider.profile_completed = False
        self.provider.save(update_fields=["legal_name", "profile_completed"])

        response = self.client.get(reverse("portal:provider_services"))

        self.assertRedirects(
            response,
            reverse("provider_complete_profile"),
            fetch_redirect_response=False,
        )

    def test_provider_service_add_view_creates_service(self):
        response = self.client.post(
            reverse("portal:provider_service_add", args=[self.service_type.service_type_id]),
            data={
                "custom_name": "Window Shine",
                "description": "Exterior window cleaning",
                "billing_unit": "fixed",
                "price": "120.50",
            },
        )

        self.assertRedirects(response, reverse("portal:provider_services"))
        service = ProviderService.objects.get(provider=self.provider, service_type=self.service_type)
        self.assertEqual(service.price_cents, 12050)
        self.assertTrue(service.is_active)
