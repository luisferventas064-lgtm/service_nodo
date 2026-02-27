import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from jobs.models import Job, PlatformLedgerEntry


def _dt(v):
    return v.isoformat(timespec="seconds") if v else None


def _money(cents):
    if cents is None:
        return None
    # CAD cents -> dollars string
    return f"{cents / 100:.2f}"


class Command(BaseCommand):
    help = "Quick status for ledger/freeze/rebuild + key totals, per job."

    def add_arguments(self, parser):
        parser.add_argument("--job-id", type=int, help="Single job id to inspect.")
        parser.add_argument("--limit", type=int, default=25, help="Max rows when listing many jobs.")
        parser.add_argument(
            "--since-days",
            type=int,
            default=None,
            help="Filter jobs updated/created in last N days.",
        )
        parser.add_argument("--only-final", action="store_true", help="Only show finalized ledger entries.")
        parser.add_argument(
            "--only-not-final",
            action="store_true",
            help="Only show NOT finalized ledger entries.",
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON.")
        parser.add_argument("--no-header", action="store_true", help="No header line in text output.")

    def handle(self, *args, **opts):
        job_id = opts.get("job_id")
        limit = opts.get("limit") or 25
        since_days = opts.get("since_days")
        only_final = bool(opts.get("only_final"))
        only_not_final = bool(opts.get("only_not_final"))
        as_json = bool(opts.get("json"))
        no_header = bool(opts.get("no_header"))

        if only_final and only_not_final:
            raise SystemExit("ERROR: choose only one: --only-final or --only-not-final")

        # Base queryset: Jobs + optional base ledger entry (non-adjustment)
        qs = Job.objects.all()

        if job_id:
            qs = qs.filter(job_id=job_id)

        if since_days is not None:
            cutoff = timezone.now() - timedelta(days=int(since_days))
            qs = qs.filter(Q(updated_at__gte=cutoff) | Q(created_at__gte=cutoff))

        # Filter by ledger final state (only if ledger exists)
        if only_final:
            qs = qs.filter(
                ledger_entry__is_adjustment=False,
                ledger_entry__is_final=True,
            )
        elif only_not_final:
            qs = qs.exclude(
                ledger_entry__is_adjustment=False,
                ledger_entry__is_final=True,
            )

        qs = qs.distinct().order_by("-job_id")[:limit] if not job_id else qs.distinct()

        rows = []
        for j in qs:
            le: PlatformLedgerEntry | None = (
                PlatformLedgerEntry.objects.filter(job_id=j.job_id, is_adjustment=False)
                .order_by("-id")
                .first()
            )

            row = {
                "job_id": j.job_id,
                "job_status": getattr(j, "status", None) or getattr(j, "job_status", None),
                "job_region": getattr(j, "tax_region_code", None) or getattr(j, "region_code", None),
                "ledger_exists": bool(le),
            }

            if le:
                row.update(
                    {
                        "currency": le.currency,
                        "tax_region_code": le.tax_region_code,
                        "gross_cents": le.gross_cents,
                        "tax_cents": le.tax_cents,
                        "fee_cents": le.fee_cents,
                        "net_provider_cents": le.net_provider_cents,
                        "platform_revenue_cents": le.platform_revenue_cents,
                        "fee_payer": le.fee_payer,
                        "is_final": le.is_final,
                        "finalized_at": _dt(le.finalized_at),
                        "finalized_run_id": le.finalized_run_id,
                        "finalize_version": le.finalize_version,
                        "rebuild_count": le.rebuild_count,
                        "last_rebuild_at": _dt(le.last_rebuild_at),
                        "last_rebuild_run_id": le.last_rebuild_run_id,
                        "last_rebuild_reason": le.last_rebuild_reason,
                    }
                )
            else:
                row.update(
                    {
                        "currency": "CAD",
                        "tax_region_code": None,
                        "gross_cents": None,
                        "tax_cents": None,
                        "fee_cents": None,
                        "net_provider_cents": None,
                        "platform_revenue_cents": None,
                        "fee_payer": None,
                        "is_final": None,
                        "finalized_at": None,
                        "finalized_run_id": None,
                        "finalize_version": None,
                        "rebuild_count": None,
                        "last_rebuild_at": None,
                        "last_rebuild_run_id": None,
                        "last_rebuild_reason": None,
                    }
                )

            rows.append(row)

        if as_json:
            self.stdout.write(
                json.dumps(
                    {
                        "generated_at": timezone.now().isoformat(timespec="seconds"),
                        "count": len(rows),
                        "rows": rows,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return

        if not no_header:
            self.stdout.write(
                "JOB  STATUS  FINAL  REBUILDS  GROSS  TAX  FEE  NET_PROV  PLAT_REV  FINALIZED_AT  FINAL_RUN_ID"
            )

        for r in rows:
            final_flag = "-" if r["is_final"] is None else ("Y" if r["is_final"] else "N")
            rebuilds = r["rebuild_count"] if r["rebuild_count"] is not None else "-"

            gross = _money(r["gross_cents"])
            tax = _money(r["tax_cents"])
            fee = _money(r["fee_cents"])
            netp = _money(r["net_provider_cents"])
            prev = _money(r["platform_revenue_cents"])

            self.stdout.write(
                f'{r["job_id"]:<4} {str(r["job_status"] or "-"):<7} {final_flag:<5} {str(rebuilds):<8} '
                f'{str(gross or "-"):<5} {str(tax or "-"):<5} {str(fee or "-"):<5} '
                f'{str(netp or "-"):<8} {str(prev or "-"):<8} '
                f'{str(r["finalized_at"] or "-"):<20} {str(r["finalized_run_id"] or "-")}'
            )

        if job_id and len(rows) == 0:
            raise SystemExit(f"ERROR: job {job_id} not found")
