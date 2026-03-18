from django.utils.translation import gettext_lazy as _


FINANCIAL_DISCLAIMER_SHORT = (
    _(
        "Displayed data is provided for informational purposes."
    )
)

FINANCIAL_DISCLAIMER_FULL_TITLE = _("Tax responsibility")

FINANCIAL_DISCLAIMER_FULL = (
    _(
        "NODO provides operational data only. Each user remains responsible for "
        "bookkeeping and tax obligations."
    )
)


def build_financial_disclaimer_context():
    return {
        "financial_disclaimer_short": FINANCIAL_DISCLAIMER_SHORT,
        "financial_disclaimer_full_title": FINANCIAL_DISCLAIMER_FULL_TITLE,
        "financial_disclaimer_full": FINANCIAL_DISCLAIMER_FULL,
    }
