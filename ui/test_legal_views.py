from django.test import TestCase
from django.urls import reverse

from core.legal_disclaimers import FINANCIAL_DISCLAIMER_FULL_TITLE


class TermsAndConditionsViewTests(TestCase):
    def test_terms_and_conditions_includes_financial_disclaimer(self):
        response = self.client.get(reverse("ui:terms_and_conditions"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, FINANCIAL_DISCLAIMER_FULL_TITLE)
        self.assertContains(
            response,
            "provided solely for informational and operational convenience",
        )
        self.assertContains(
            response,
            "professional accounting, tax, or legal advice",
        )
