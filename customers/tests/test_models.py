"""Unit tier: model logic in isolation, no HTTP."""

from django.test import TestCase

from accounts.models import Organisation
from customers.models import Customer


class HealthCategoryTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def _customer(self, score):
        return Customer.objects.create(organisation=self.org, name="Some Co", health_score=score)

    def test_score_at_or_above_70_is_good(self):
        self.assertEqual(self._customer(70).health_category, Customer.HealthCategory.GOOD)
        self.assertEqual(self._customer(100).health_category, Customer.HealthCategory.GOOD)

    def test_score_40_to_69_is_average(self):
        self.assertEqual(self._customer(40).health_category, Customer.HealthCategory.AVERAGE)
        self.assertEqual(self._customer(69).health_category, Customer.HealthCategory.AVERAGE)

    def test_score_below_40_is_poor(self):
        self.assertEqual(self._customer(0).health_category, Customer.HealthCategory.POOR)
        self.assertEqual(self._customer(39).health_category, Customer.HealthCategory.POOR)
