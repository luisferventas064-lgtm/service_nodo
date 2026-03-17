from django.utils.translation import gettext_lazy as _


FINANCIAL_DISCLAIMER_SHORT = (
    _(
        "Financial and activity data displayed on this page is provided for "
        "informational and operational purposes only. NODO does not provide "
        "accounting, tax, payroll, or legal services and does not file, calculate, "
        "collect, or pay taxes on behalf of clients, providers, or workers. Each "
        "user is solely responsible for maintaining their own records and complying "
        "with all applicable tax, accounting, and legal obligations."
    )
)

FINANCIAL_DISCLAIMER_FULL_TITLE = _("Financial Reporting and Tax Responsibility")

FINANCIAL_DISCLAIMER_FULL = (
    _(
        "Any financial, revenue, earnings, or activity information displayed on the "
        "NODO platform is provided solely for informational and operational "
        "convenience. Such information may include summaries, analytics, exports, "
        "reports, or other data derived from platform activity.\n\n"
        "NODO does not provide accounting, tax, payroll, or legal services. NODO "
        "does not calculate, collect, remit, report, or pay taxes on behalf of "
        "clients, providers, or workers unless explicitly stated in a separate "
        "written agreement.\n\n"
        "All clients, providers, and workers using the platform remain solely "
        "responsible for maintaining accurate financial records, determining their "
        "tax obligations, filing required tax returns, and complying with all "
        "applicable federal, provincial, state, or local laws and regulations.\n\n"
        "The information provided by the platform should not be relied upon as "
        "professional accounting, tax, or legal advice. Users should consult their "
        "own accountants, tax advisors, or legal professionals when necessary."
    )
)


def build_financial_disclaimer_context():
    return {
        "financial_disclaimer_short": FINANCIAL_DISCLAIMER_SHORT,
        "financial_disclaimer_full_title": FINANCIAL_DISCLAIMER_FULL_TITLE,
        "financial_disclaimer_full": FINANCIAL_DISCLAIMER_FULL,
    }
