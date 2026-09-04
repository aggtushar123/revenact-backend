from decimal import Decimal

from django.test import TestCase

from services.accounts.models import Organisation
from services.fx_rates.conversion import convert_to_org_currency
from services.fx_rates.models import FxRate


class ConvertToOrgCurrencyTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")

    def test_same_currency_is_a_no_op(self):
        result = convert_to_org_currency(Decimal("100.00"), "USD", self.org)
        self.assertEqual(result, Decimal("100.00"))

    def test_converts_using_the_configured_rate(self):
        FxRate.objects.create(organisation=self.org, currency="EUR", rate_to_org_currency="1.08")
        result = convert_to_org_currency(Decimal("100.00"), "EUR", self.org)
        self.assertEqual(result, Decimal("108.000000"))

    def test_returns_none_when_no_rate_is_configured(self):
        result = convert_to_org_currency(Decimal("100.00"), "EUR", self.org)
        self.assertIsNone(result)

    def test_only_uses_the_rate_for_the_requested_organisation(self):
        other_org = Organisation.objects.create(name="Other Inc", currency="USD")
        FxRate.objects.create(organisation=other_org, currency="EUR", rate_to_org_currency="1.08")
        result = convert_to_org_currency(Decimal("100.00"), "EUR", self.org)
        self.assertIsNone(result)
