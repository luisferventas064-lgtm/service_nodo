# jobs/fees.py

from dataclasses import dataclass
from typing import Literal, Dict, Optional

FeeModel = Literal["percentage", "fixed"]
FeePayer = Literal["client", "provider"]

@dataclass(frozen=True)
class FeeRule:
    model: FeeModel
    payer: FeePayer
    value_bps: int = 0      # percentage (1000 = 10.00%)
    value_cents: int = 0    # fixed cents


# ✅ v1: reglas por provincia (se migra a DB después)
FEE_RULES_BY_REGION: Dict[str, FeeRule] = {
    # Quebec (ejemplo)
    "QC": FeeRule(model="percentage", payer="client", value_bps=1000),

    # Ontario (ejemplo)
    "ON": FeeRule(model="percentage", payer="client", value_bps=800),

    # fallback genérico
    "DEFAULT": FeeRule(model="percentage", payer="client", value_bps=1000),
}


def get_on_demand_fee_rule_for_region(region_code: Optional[str]) -> FeeRule:
    if not region_code:
        return FEE_RULES_BY_REGION["DEFAULT"]
    return FEE_RULES_BY_REGION.get(region_code.upper(), FEE_RULES_BY_REGION["DEFAULT"])


def compute_fee_cents(subtotal_cents: int, rule: FeeRule) -> int:
    if subtotal_cents < 0:
        raise ValueError("subtotal_cents must be >= 0")

    if rule.model == "percentage":
        if rule.value_bps < 0:
            raise ValueError("value_bps must be >= 0")
        # redondeo normal (half-up)
        return int((subtotal_cents * rule.value_bps + 5000) // 10000)

    if rule.model == "fixed":
        if rule.value_cents < 0:
            raise ValueError("value_cents must be >= 0")
        return int(rule.value_cents)

    raise ValueError("unknown fee model")
