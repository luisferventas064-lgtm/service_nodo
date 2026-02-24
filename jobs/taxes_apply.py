# jobs/taxes_apply.py

from jobs.taxes import compute_tax_cents, get_tax_rule_for_region


def apply_tax_snapshot_to_line(line, *, region_code: str | None):
    """
    line: ProviderTicketLine o ClientTicketLine
    region_code: snapshot de ticket.tax_region_code
    """
    rule = get_tax_rule_for_region(region_code)

    line.tax_region_code = region_code or None
    line.tax_rate_bps = rule.rate_bps
    line.tax_cents = compute_tax_cents(int(line.line_total_cents or 0), rule)
