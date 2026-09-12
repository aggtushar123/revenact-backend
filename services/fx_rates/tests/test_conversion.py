from decimal import Decimal

from django.test import TestCase

from services.accounts.models import Organisation
from services.fx_rates.conversion import convert_to_org_currency, rates_for
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


class BulkRatesTests(TestCase):
    """The pre-fetched path, for callers converting a page of rows at once."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        FxRate.objects.create(organisation=self.org, currency="EUR", rate_to_org_currency="1.08")

    def test_rates_for_returns_only_this_organisations_rates(self):
        other = Organisation.objects.create(name="Other Inc", currency="USD")
        FxRate.objects.create(organisation=other, currency="GBP", rate_to_org_currency="1.25")

        self.assertEqual(rates_for(self.org), {"EUR": Decimal("1.08")})

    def test_a_prefetched_table_gives_the_same_answer_as_a_query(self):
        rates = rates_for(self.org)

        self.assertEqual(
            convert_to_org_currency(Decimal("100.00"), "EUR", self.org, rates=rates),
            convert_to_org_currency(Decimal("100.00"), "EUR", self.org),
        )

    def test_a_currency_missing_from_the_table_is_still_no_number(self):
        # The "no rate means no figure" rule has to hold on both paths, or a
        # bulk caller would quietly sum an unconverted amount.
        self.assertIsNone(
            convert_to_org_currency(Decimal("100.00"), "JPY", self.org, rates=rates_for(self.org))
        )

    def test_the_prefetched_path_runs_no_queries(self):
        rates = rates_for(self.org)

        with self.assertNumQueries(0):
            convert_to_org_currency(Decimal("100.00"), "EUR", self.org, rates=rates)
            convert_to_org_currency(Decimal("100.00"), "JPY", self.org, rates=rates)
