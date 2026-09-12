"""The renewal dates the demo seeders write.

Demo plumbing, but one property of it is load-bearing: the seeded book has to
stay meaningful as real time moves past the literal dates in the seed files.
Every renewal chart asks a rolling question ("what renews in the next 90 days"),
and a book of 2026 dates answers all of them with silence the moment it is 2027
— which looks exactly like a broken integration rather than a quiet quarter.
"""

from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.management.commands.seed_demo_customers import (
    DEMO_OVERDUE_DAYS,
    upcoming_anniversary,
)
from services.customers.models import Customer


class UpcomingAnniversaryTests(SimpleTestCase):
    def test_a_future_date_is_left_alone(self):
        anniversary = date(2026, 3, 2)
        self.assertEqual(upcoming_anniversary(anniversary, date(2026, 1, 1)), anniversary)

    def test_a_past_date_rolls_to_the_same_day_next_year(self):
        # A March renewal stays a March renewal: the contract renewed, it didn't
        # move to whenever the seed happened to run.
        self.assertEqual(
            upcoming_anniversary(date(2026, 3, 2), date(2026, 9, 12)), date(2027, 3, 2)
        )

    def test_it_rolls_as_many_years_as_it_takes(self):
        self.assertEqual(
            upcoming_anniversary(date(2020, 6, 15), date(2026, 9, 12)), date(2027, 6, 15)
        )

    def test_todays_date_counts_as_upcoming(self):
        today = date(2026, 9, 12)
        self.assertEqual(upcoming_anniversary(today, today), today)

    def test_a_leap_day_falls_back_rather_than_raising(self):
        self.assertEqual(
            upcoming_anniversary(date(2024, 2, 29), date(2026, 1, 1)), date(2026, 2, 28)
        )


class SeedRenewalSpreadTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )

    def _seed(self):
        call_command("seed_demo_customers", org_email="alice@acme.io", stdout=StringIO())

    def test_almost_every_seeded_renewal_is_in_the_future(self):
        self._seed()
        today = timezone.localdate()

        past = Customer.objects.filter(organisation=self.org, renewal_date__lt=today)

        # Only the deliberately overdue ones, which the dashboard has a tile for.
        self.assertTrue(set(past.values_list("name", flat=True)) <= set(DEMO_OVERDUE_DAYS))

    def test_the_deliberately_overdue_ones_really_are_overdue(self):
        self._seed()
        today = timezone.localdate()

        for name, days in DEMO_OVERDUE_DAYS.items():
            customer = Customer.objects.filter(organisation=self.org, name=name).first()
            if customer is None:
                continue  # not every demo company is seeded into every org
            self.assertEqual(customer.renewal_date, today - timedelta(days=days))

    def test_the_book_spreads_across_more_than_one_quarter(self):
        # A renewal calendar where everything lands in one bar teaches nobody
        # anything about the shape of the book.
        self._seed()

        quarters = {
            (c.renewal_date.year, (c.renewal_date.month - 1) // 3)
            for c in Customer.objects.filter(organisation=self.org, renewal_date__isnull=False)
        }

        self.assertGreaterEqual(len(quarters), 3)

    def test_re_running_is_stable(self):
        self._seed()
        before = dict(
            Customer.objects.filter(organisation=self.org).values_list("name", "renewal_date")
        )

        self._seed()

        after = dict(
            Customer.objects.filter(organisation=self.org).values_list("name", "renewal_date")
        )
        self.assertEqual(before, after)
