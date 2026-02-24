from django.db import transaction

from clients.lines import ensure_client_base_line
from clients.lines_fee import ensure_client_fee_line
from clients.ticketing import ensure_client_ticket
from jobs.models import Job
from jobs.services_fee import recompute_on_demand_fee_for_open_tickets
from assignments.models import JobAssignment
from providers.lines import ensure_provider_base_line
from providers.lines_fee import ensure_provider_fee_line
from providers.ticketing import ensure_provider_ticket


def _normalize_country_code(country: str | None) -> str:
    if not country:
        return ""
    value = str(country).strip().upper()
    if value in {"CANADA", "CA"}:
        return "CA"
    if value in {"UNITED STATES", "USA", "US"}:
        return "US"
    if len(value) == 2:
        return value
    return value[:2]


def _build_tax_region_code(job: Job) -> str:
    country_code = _normalize_country_code(getattr(job, "country", ""))
    province_code = str(getattr(job, "province", "") or "").strip().upper()
    if country_code and province_code:
        return f"{country_code}-{province_code}"
    return country_code or province_code


def _activate_assignment_for_job(job: Job):
    # Desactiva cualquier assignment activo previo para este job
    JobAssignment.objects.filter(job=job, is_active=True).update(is_active=False)

    # Activa/crea el assignment para el provider seleccionado
    assignment, created = JobAssignment.objects.get_or_create(
        job=job,
        provider=job.selected_provider,
        defaults={"is_active": True},
    )

    if not created and not assignment.is_active:
        assignment.is_active = True
        assignment.save(update_fields=["is_active"])

    return assignment


@transaction.atomic
def confirm_normal_job_by_client(*, job_id: int, client_id: int):
    job = Job.objects.select_for_update().get(pk=job_id)

    if job.job_status != "pending_client_confirmation":
        return False, "INVALID_JOB_STATUS"

    if job.client_id != client_id:
        return False, "CLIENT_NOT_ALLOWED_FOR_THIS_JOB"

    job.job_status = "assigned"
    job.save(update_fields=["job_status", "updated_at"])

    assignment = _activate_assignment_for_job(job)
    tax_region_code = _build_tax_region_code(job)
    pt = ensure_provider_ticket(
        provider_id=assignment.provider_id,
        ref_type="job",
        ref_id=job.job_id,
        stage="estimate",
        status="open",
        subtotal_cents=0,
        tax_cents=0,
        total_cents=0,
        currency="CAD",
        tax_region_code=tax_region_code,
    )
    ensure_provider_base_line(
        pt.pk,
        description="Service (estimate)",
        unit_price_cents=pt.subtotal_cents or 0,
        tax_cents=pt.tax_cents or 0,
        tax_region_code=pt.tax_region_code or "",
        tax_code="",
    )
    if job.client_id:
        ct = ensure_client_ticket(
            client_id=job.client_id,
            ref_type="job",
            ref_id=job.job_id,
            stage="estimate",
            status="open",
            subtotal_cents=0,
            tax_cents=0,
            total_cents=0,
            currency="CAD",
            tax_region_code=tax_region_code,
        )
        ensure_client_base_line(
            ct.pk,
            description="Service (estimate)",
            unit_price_cents=ct.subtotal_cents or 0,
            tax_cents=ct.tax_cents or 0,
            tax_region_code=ct.tax_region_code or "",
            tax_code="",
        )
        if job.job_mode == Job.JobMode.ON_DEMAND:
            ensure_provider_fee_line(pt.pk, amount_cents=0)
            ensure_client_fee_line(ct.pk, amount_cents=0)
            recompute_on_demand_fee_for_open_tickets(pt.pk, ct.pk)

    return True, job, assignment
