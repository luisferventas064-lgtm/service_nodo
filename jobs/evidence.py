from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from django.conf import settings
from django.utils import timezone

from clients.models import ClientTicket
from jobs.models import Job, PlatformLedgerEntry
from providers.models import ProviderTicket


def _safe_int(v) -> int:
    return int(v or 0)


def _safe_json_value(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return str(v)
    return v


def _line_to_dict(line) -> dict[str, Any]:
    return {
        "id": getattr(line, "pk", None),
        "description": getattr(line, "description", None),
        "line_type": getattr(line, "line_type", None),
        "qty": _safe_json_value(getattr(line, "qty", None)),
        "unit_price_cents": _safe_int(getattr(line, "unit_price_cents", 0)),
        "line_total_cents": _safe_int(getattr(line, "line_total_cents", 0)),
        "tax_cents": _safe_int(getattr(line, "tax_cents", 0)),
        "tax_rate_bps": _safe_int(getattr(line, "tax_rate_bps", 0)),
        "tax_region_code": getattr(line, "tax_region_code", None),
        "tax_code": getattr(line, "tax_code", None),
        "meta": _safe_json_value(getattr(line, "meta", None)),
        "created_at": getattr(line, "created_at", None).isoformat() if getattr(line, "created_at", None) else None,
        "updated_at": getattr(line, "updated_at", None).isoformat() if getattr(line, "updated_at", None) else None,
    }


def _ticket_to_dict(ticket) -> dict[str, Any]:
    lines = list(ticket.lines.all()) if ticket else []
    total_cents = _safe_int(getattr(ticket, "total_cents", 0))
    return {
        "id": getattr(ticket, "pk", None),
        "ticket_no": getattr(ticket, "ticket_no", None),
        "ref_type": getattr(ticket, "ref_type", None),
        "ref_id": getattr(ticket, "ref_id", None),
        "stage": getattr(ticket, "stage", None),
        "status": getattr(ticket, "status", None),
        "currency": getattr(ticket, "currency", None),
        "tax_region_code": getattr(ticket, "tax_region_code", None),
        "gross_cents": total_cents,
        "tax_cents": _safe_int(getattr(ticket, "tax_cents", 0)),
        "subtotal_cents": _safe_int(getattr(ticket, "subtotal_cents", 0)),
        "total_cents": total_cents,
        "lines": [_line_to_dict(l) for l in lines],
        "created_at": getattr(ticket, "created_at", None).isoformat() if getattr(ticket, "created_at", None) else None,
        "updated_at": getattr(ticket, "updated_at", None).isoformat() if getattr(ticket, "updated_at", None) else None,
    }


def _ledger_to_dict(entry: PlatformLedgerEntry) -> dict[str, Any]:
    return {
        "job_id": entry.job_id,
        "currency": entry.currency,
        "tax_region_code": entry.tax_region_code,
        "gross_cents": entry.gross_cents,
        "tax_cents": entry.tax_cents,
        "fee_cents": entry.fee_cents,
        "net_provider_cents": entry.net_provider_cents,
        "platform_revenue_cents": entry.platform_revenue_cents,
        "fee_payer": entry.fee_payer,
        "is_final": entry.is_final,
        "finalized_at": entry.finalized_at.isoformat() if entry.finalized_at else None,
        "finalized_run_id": entry.finalized_run_id,
        "finalize_version": entry.finalize_version,
        "rebuild_count": entry.rebuild_count,
        "last_rebuild_at": entry.last_rebuild_at.isoformat() if entry.last_rebuild_at else None,
        "last_rebuild_run_id": entry.last_rebuild_run_id,
        "last_rebuild_reason": entry.last_rebuild_reason,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


def _get_provider_ticket(job: Job) -> ProviderTicket | None:
    qs = ProviderTicket.objects.filter(ref_type="job", ref_id=job.job_id)
    if job.selected_provider_id:
        qs = qs.filter(provider_id=job.selected_provider_id)
    return qs.order_by("-provider_ticket_id").first()


def _get_client_ticket(job: Job) -> ClientTicket | None:
    qs = ClientTicket.objects.filter(ref_type="job", ref_id=job.job_id)
    if job.client_id:
        qs = qs.filter(client_id=job.client_id)
    return qs.order_by("-client_ticket_id").first()


def build_job_evidence_payload(
    job: Job,
    *,
    run_id: Optional[str] = None,
    source: str = "finalize",
) -> dict[str, Any]:
    job = Job.objects.get(pk=job.pk)
    entry = PlatformLedgerEntry.objects.get(job_id=job.job_id)
    provider_ticket = _get_provider_ticket(job)
    client_ticket = _get_client_ticket(job)

    return {
        "meta": {
            "generated_at": timezone.now().isoformat(),
            "run_id": run_id,
            "source": source,
            "schema_version": 1,
        },
        "job": {
            "job_id": job.job_id,
            "status": job.job_status,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "country": getattr(job, "country", None),
            "province": getattr(job, "province", None),
            "city": getattr(job, "city", None),
            "postal_code": getattr(job, "postal_code", None),
        },
        "ledger": _ledger_to_dict(entry),
        "tickets": {
            "provider": _ticket_to_dict(provider_ticket) if provider_ticket else None,
            "client": _ticket_to_dict(client_ticket) if client_ticket else None,
        },
    }


def write_job_evidence_json(
    job_id: int,
    *,
    out_dir: Optional[str] = None,
    run_id: Optional[str] = None,
    source: str = "finalize",
) -> str:
    job = Job.objects.get(pk=job_id)
    payload = build_job_evidence_payload(job, run_id=run_id, source=source)

    base = Path(out_dir) if out_dir else Path(getattr(settings, "BASE_DIR", ".")) / "evidence"
    base.mkdir(parents=True, exist_ok=True)

    ts = timezone.now().strftime("%Y%m%d_%H%M%S")
    path = base / f"ledger_evidence_job_{job_id}_{ts}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return str(path)


def try_write_job_evidence_json(
    job_id: int,
    *,
    out_dir: Optional[str] = None,
    run_id: Optional[str] = None,
    source: str = "finalize",
) -> Optional[str]:
    """
    Best-effort evidence writer.
    Must never raise to callers in critical flows (close/rebuild).
    Returns path or None.
    """
    try:
        return write_job_evidence_json(job_id, out_dir=out_dir, run_id=run_id, source=source)
    except Exception:
        return None
