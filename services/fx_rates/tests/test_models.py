from django.db import IntegrityError
from django.test import TestCase

from services.accounts.models import Organisation
from services.fx_rates.models import FxRate


class FxRateModelTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")

    def test_str_and_defaults(self):
        rate = FxRate.objects.create(
            organisation=self.org, currency="EUR", rate_to_org_currency="1.080000"
        )
        self.assertIn("1 EUR", str(rate))
        self.assertIsNotNone(rate.created_at)
        self.assertIsNotNone(rate.updated_at)

    def test_one_rate_per_currency_per_organisation(self):
        FxRate.objects.create(organisation=self.org, currency="EUR", rate_to_org_currency="1.08")
        with self.assertRaises(IntegrityError):
            FxRate.objects.create(
                organisation=self.org, currency="EUR", rate_to_org_currency="1.10"
            )

    def test_same_currency_allowed_across_different_organisations(self):
        other_org = Organisation.objects.create(name="Other Inc", currency="GBP")
        FxRate.objects.create(organisation=self.org, currency="EUR", rate_to_org_currency="1.08")
        # No IntegrityError — the unique constraint is per-organisation.
        FxRate.objects.create(organisation=other_org, currency="EUR", rate_to_org_currency="0.85")
        self.assertEqual(FxRate.objects.count(), 2)
