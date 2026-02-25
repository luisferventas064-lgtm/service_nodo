import json
import os

from django.conf import settings
from django.utils import timezone


def write_settlement_evidence(settlement, event_type, extra=None):
    base_path = os.path.join(settings.BASE_DIR, "evidence", "settlements")
    os.makedirs(base_path, exist_ok=True)

    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{event_type}_{settlement.id}_{timestamp}.json"
    full_path = os.path.join(base_path, filename)

    payload = {
        "event_type": event_type,
        "timestamp": timezone.now().isoformat(),
        "settlement_id": settlement.id,
        "provider_id": settlement.provider_id,
        "period_start": settlement.period_start.isoformat(),
        "period_end": settlement.period_end.isoformat(),
        "currency": settlement.currency,
        "status": settlement.status,
        "totals": {
            "gross_cents": settlement.total_gross_cents,
            "tax_cents": settlement.total_tax_cents,
            "fee_cents": settlement.total_fee_cents,
            "net_provider_cents": settlement.total_net_provider_cents,
            "platform_revenue_cents": settlement.total_platform_revenue_cents,
            "total_jobs": settlement.total_jobs,
        },
        "extra": extra or {},
    }

    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4)

    return full_path
