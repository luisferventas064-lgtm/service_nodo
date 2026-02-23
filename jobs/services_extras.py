from __future__ import annotations

from django.db import transaction

from clients.models import ClientTicket, ClientTicketLine
from jobs.models import Job
from providers.models import ProviderTicket, ProviderTicketLine


def _resolve_assigned_provider_id(job: Job) -> int | None:
    from assignments.models import JobAssignment

    active_assignment = JobAssignment.objects.filter(
        job_id=job.job_id,
        is_active=True,
    ).first()
    if active_assignment:
        return active_assignment.provider_id
    return job.selected_provider_id


@transaction.atomic
def add_extra_line_for_job(
    *,
    job_id: int,
    provider_id: int,
    description: str,
    amount_cents: int,
) -> dict:
    """
    Crea un EXTRA line espejo:
      - ProviderTicketLine
      - ClientTicketLine
    Por ahora tax=0 (tax engine viene despues).
    Permiso: solo provider asignado al job.
    """
    job = Job.objects.select_for_update().get(pk=job_id)

    assigned_provider_id = _resolve_assigned_provider_id(job)
    if not assigned_provider_id or assigned_provider_id != provider_id:
        raise PermissionError("provider_not_allowed")

    pt = ProviderTicket.objects.select_for_update().get(
        ref_type="job",
        ref_id=job_id,
        provider_id=provider_id,
    )
    ct = ClientTicket.objects.select_for_update().get(
        ref_type="job",
        ref_id=job_id,
        client_id=job.client_id,
    )

    if pt.status != "open" or ct.status != "open":
        raise PermissionError("ticket_not_open")

    next_no_pt = (pt.lines.order_by("-line_no").values_list("line_no", flat=True).first() or 0) + 1
    next_no_ct = (ct.lines.order_by("-line_no").values_list("line_no", flat=True).first() or 0) + 1

    p_line = ProviderTicketLine.objects.create(
        ticket=pt,
        line_no=next_no_pt,
        line_type="extra",
        description=description,
        qty=1,
        unit_price_cents=amount_cents,
        line_subtotal_cents=amount_cents,
        tax_cents=0,
        line_total_cents=amount_cents,
        tax_region_code=pt.tax_region_code or "",
        tax_code="",
        meta={},
    )

    c_line = ClientTicketLine.objects.create(
        ticket=ct,
        line_no=next_no_ct,
        line_type="extra",
        description=description,
        qty=1,
        unit_price_cents=amount_cents,
        line_subtotal_cents=amount_cents,
        tax_cents=0,
        line_total_cents=amount_cents,
        tax_region_code=ct.tax_region_code or "",
        tax_code="",
        meta={},
    )

    # Tus signals ya recalculan totals
    return {
        "ok": True,
        "job_id": job_id,
        "provider_id": provider_id,
        "provider_ticket_id": pt.pk,
        "client_ticket_id": ct.pk,
        "provider_line_id": p_line.pk,
        "client_line_id": c_line.pk,
        "amount_cents": amount_cents,
    }
