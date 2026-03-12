from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class TaxRule:
    """
    Snapshot-friendly: un solo rate combinado por region.
    Ej: 14975 = 14.975%
    """

    rate_bps: int


# v1: reglas por provincia / region (luego se puede mover a DB)
TAX_RULES_BY_REGION: Dict[str, TaxRule] = {
    # Quebec: GST 5% + QST 9.975% = 14.975%
    "QC": TaxRule(rate_bps=14975),
    # Ontario: HST 13%
    "ON": TaxRule(rate_bps=13000),
    # Alberta: GST 5%
    "AB": TaxRule(rate_bps=5000),
    # British Columbia: GST 5% + PST 7%
    "BC": TaxRule(rate_bps=12000),
    # Manitoba: GST 5% + PST 7%
    "MB": TaxRule(rate_bps=12000),
    # Atlantic provinces with HST 15%
    "NB": TaxRule(rate_bps=15000),
    "NL": TaxRule(rate_bps=15000),
    "NS": TaxRule(rate_bps=15000),
    "PE": TaxRule(rate_bps=15000),
    # Saskatchewan: GST 5% + PST 6%
    "SK": TaxRule(rate_bps=11000),
    # Territories: GST 5%
    "NT": TaxRule(rate_bps=5000),
    "NU": TaxRule(rate_bps=5000),
    "YT": TaxRule(rate_bps=5000),
    # Default (si no hay region)
    "DEFAULT": TaxRule(rate_bps=0),
}


def get_tax_rule_for_region(region_code: Optional[str]) -> TaxRule:
    if not region_code:
        return TAX_RULES_BY_REGION["DEFAULT"]
    return TAX_RULES_BY_REGION.get(region_code.upper(), TAX_RULES_BY_REGION["DEFAULT"])


def compute_tax_cents(line_total_cents: int, rule: TaxRule) -> int:
    if line_total_cents < 0:
        raise ValueError("line_total_cents must be >= 0")
    if rule.rate_bps < 0:
        raise ValueError("rate_bps must be >= 0")

    # redondeo half-up a centavos
    return int((line_total_cents * rule.rate_bps + 50000) // 100000)
