"""Unit tier: model logic in isolation, no HTTP."""

from django.test import TestCase

from services.accounts.models import Organisation
from services.customers.models import Account, Customer


class HealthCategoryTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def _customer(self, score):
        return Customer.objects.create(organisation=self.org, name="Some Co", health_score=score)

    def test_score_at_or_above_7_is_good(self):
        self.assertEqual(self._customer(7.0).health_category, Customer.HealthCategory.GOOD)
        self.assertEqual(self._customer(9.9).health_category, Customer.HealthCategory.GOOD)

    def test_score_4_to_6point9_is_average(self):
        self.assertEqual(self._customer(4.0).health_category, Customer.HealthCategory.AVERAGE)
        self.assertEqual(self._customer(6.9).health_category, Customer.HealthCategory.AVERAGE)

    def test_score_below_4_is_poor(self):
        self.assertEqual(self._customer(0.0).health_category, Customer.HealthCategory.POOR)
        self.assertEqual(self._customer(3.9).health_category, Customer.HealthCategory.POOR)


class SeatUtilizationTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def _customer(self, contracted, active):
        return Customer.objects.create(
            organisation=self.org,
            name="Some Co",
            total_contracted_seats=contracted,
            total_active_seats=active,
        )

    def test_computed_from_active_and_contracted_seats(self):
        customer = self._customer(contracted=560, active=471)
        self.assertEqual(customer.seat_utilization_percentage, 84.11)

    def test_none_when_no_contracted_seats_recorded(self):
        customer = Customer.objects.create(organisation=self.org, name="Some Co")
        self.assertIsNone(customer.seat_utilization_percentage)

    def test_none_when_contracted_seats_is_zero(self):
        customer = self._customer(contracted=0, active=0)
        self.assertIsNone(customer.seat_utilization_percentage)


class AccountHealthCategoryTests(TestCase):
    """Account.health_category reuses Customer.HEALTH_THRESHOLDS directly
    (see Account model's docstring) — same thresholds, same behavior,
    just pinned down again here in case that ever drifts."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")

    def _account(self, score):
        return Account.objects.create(
            customer=self.customer, name="Some Region", health_score=score
        )

    def test_score_at_or_above_7_is_good(self):
        self.assertEqual(self._account(7.0).health_category, Customer.HealthCategory.GOOD)
        self.assertEqual(self._account(9.9).health_category, Customer.HealthCategory.GOOD)

    def test_score_4_to_6point9_is_average(self):
        self.assertEqual(self._account(4.0).health_category, Customer.HealthCategory.AVERAGE)
        self.assertEqual(self._account(6.9).health_category, Customer.HealthCategory.AVERAGE)

    def test_score_below_4_is_poor(self):
        self.assertEqual(self._account(0.0).health_category, Customer.HealthCategory.POOR)
        self.assertEqual(self._account(3.9).health_category, Customer.HealthCategory.POOR)
