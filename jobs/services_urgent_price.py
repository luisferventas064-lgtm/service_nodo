from decimal import Decimal, ROUND_HALF_UP
from jobs.models import Job

MONEY_Q = Decimal("0.01")


def _money(x: Decimal) -> Decimal:
    return x.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def compute_urgent_price(job: Job) -> tuple[Decimal, Decimal]:
    """
    Returns:
      (urgent_total, urgent_fee_amount)
    Ambos redondeados a 2 decimales con ROUND_HALF_UP.
    """
    if not job.quoted_base_price:
        return (Decimal("0.00"), Decimal("0.00"))

    base = Decimal(job.quoted_base_price)
    fee_type = job.quoted_emergency_fee_type
    fee_value = Decimal(job.quoted_emergency_fee_value or Decimal("0.00"))

    if fee_type == "fixed":
        fee_amount = _money(fee_value)
        total = _money(base + fee_amount)
        return (total, fee_amount)

    if fee_type == "percent":
        fee_amount = _money((base * fee_value) / Decimal("100"))
        total = _money(base + fee_amount)
        return (total, fee_amount)

    return (_money(base), Decimal("0.00"))
