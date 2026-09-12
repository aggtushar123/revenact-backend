"""The scheduled job: recalculate scores, record the monthly snapshot."""

from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from services.accounts.models import Organisation, User
from services.customers.management.commands.run_health_maintenance import (
    last_completed_month_end,
)
from services.customers.models import Customer, HealthSnapshot, Product


class LastCompletedMonthEndTests(TestCase):
    def test_returns_the_previous_months_last_day(self):
        self.assertEqual(last_completed_month_end(date(2026, 9, 11)), date(2026, 8, 31))
        self.assertEqual(last_completed_month_end(date(2026, 3, 1)), date(2026, 2, 28))

    def test_crosses_the_year_boundary(self):
        self.assertEqual(last_completed_month_end(date(2026, 1, 1)), date(2025, 12, 31))
        self.assertEqual(last_completed_month_end(date(2027, 1, 15)), date(2026, 12, 31))

    def test_handles_a_leap_february(self):
        self.assertEqual(last_completed_month_end(date(2028, 3, 5)), date(2028, 2, 29))


class RunHealthMaintenanceTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.other_org = Organisation.objects.create(name="Globex")
        User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.customer = Customer.objects.create(
            organisation=self.org,
            name="Some Co",
            health_score="5.0",
            csm_pulse_score=4,
            ai_pulse_value=2,
            total_active_seats=50,
            total_contracted_seats=100,
            primary_product=Product.objects.create(organisation=self.org, name="Product A"),
        )
        self.month_end = last_completed_month_end()

    def run_command(self, *args):
        out = StringIO()
        call_command("run_health_maintenance", *args, stdout=out)
        return out.getvalue()

    def test_records_a_snapshot_for_the_last_completed_month(self):
        self.run_command()

        snapshot = HealthSnapshot.objects.get(customer=self.customer)
        self.assertEqual(snapshot.captured_on, self.month_end)
        self.assertEqual(snapshot.csm_pulse_score, 4)
        self.assertEqual(snapshot.ai_pulse_value, 2)

    def test_recalculates_the_score_before_recording_it(self):
        # The snapshot should carry the freshly computed value, not the stale
        # one the row happened to be sitting on.
        self.run_command()

        self.customer.refresh_from_db()
        snapshot = HealthSnapshot.objects.get(customer=self.customer)
        self.assertNotEqual(self.customer.health_score, Decimal("5.0"))
        self.assertEqual(snapshot.health_score, self.customer.health_score)

    def test_running_twice_records_one_snapshot(self):
        self.run_command()
        self.run_command()
        self.assertEqual(HealthSnapshot.objects.filter(customer=self.customer).count(), 1)

    def test_does_not_overwrite_a_month_already_recorded(self):
        # That row records how the month *ended*. Re-running later and
        # upserting it would replace it with the following month's values, so
        # the history would drift forwards every time the job ran.
        HealthSnapshot.objects.create(
            customer=self.customer, captured_on=self.month_end, health_score="1.0"
        )
        self.run_command()

        snapshot = HealthSnapshot.objects.get(customer=self.customer)
        self.assertEqual(snapshot.health_score, Decimal("1.0"))

    def test_reports_what_it_did(self):
        output = self.run_command()
        self.assertIn("recorded 1 snapshot(s)", output)
        self.assertIn(str(self.month_end), output)

    def test_dry_run_writes_nothing(self):
        # Reloaded first: a DecimalField holds whatever was assigned to it
        # until the row round-trips, so setUp's "5.0" is still a str here.
        self.customer.refresh_from_db()
        before = self.customer.health_score
        output = self.run_command("--dry-run")

        self.customer.refresh_from_db()
        self.assertEqual(HealthSnapshot.objects.count(), 0)
        self.assertEqual(self.customer.health_score, before)
        self.assertIn("would record", output)

    def test_can_be_limited_to_one_tenant(self):
        Customer.objects.create(organisation=self.other_org, name="Not Mine")
        self.run_command("--org-email", "alice@acme.io")

        self.assertEqual(HealthSnapshot.objects.count(), 1)
        self.assertEqual(HealthSnapshot.objects.get().customer, self.customer)

    def test_leaves_an_overridden_score_alone(self):
        self.customer.health_score_override = Decimal("9.9")
        self.customer.health_score = Decimal("9.9")
        self.customer.save()

        self.run_command()

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.health_score, Decimal("9.9"))
        # Still snapshotted — the pinned score is what that month ended on.
        self.assertEqual(
            HealthSnapshot.objects.get(customer=self.customer).health_score, Decimal("9.9")
        )

    def test_handles_a_book_with_nothing_to_record(self):
        Customer.objects.all().delete()
        output = self.run_command()
        self.assertIn("recorded 0 snapshot(s)", output)
