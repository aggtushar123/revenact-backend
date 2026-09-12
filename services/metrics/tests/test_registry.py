"""The metric layer reads the dashboards' own rollups, whole-org."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.models import Customer, Ticket
from services.customers.scoping import SystemActor, visible_customers
from services.metrics import registry
from services.metrics.models import MetricSnapshot
from services.metrics.recording import record_period_end


class SystemActorTests(TestCase):
    def test_sees_every_customer_in_the_organisation_whoever_owns_them(self):
        org = Organisation.objects.create(name="Acme Inc", currency="USD")
        carl = User.objects.create_user(
            email="carl@acme.io", password="x", name="Carl", organisation=org, role=User.Role.CSM
        )
        dana = User.objects.create_user(
            email="dana@acme.io", password="x", name="Dana", organisation=org, role=User.Role.CSM
        )
        Customer.objects.create(organisation=org, name="Carl's", owner=carl)
        Customer.objects.create(organisation=org, name="Dana's", owner=dana)
        Customer.objects.create(organisation=org, name="Nobody's")

        self.assertEqual(visible_customers(SystemActor(org)).count(), 3)
        # A CSM, for contrast, sees their own plus the unowned.
        self.assertEqual(visible_customers(carl).count(), 2)

    def test_never_sees_another_organisation(self):
        org = Organisation.objects.create(name="Acme Inc", currency="USD")
        other = Organisation.objects.create(name="Other Inc", currency="USD")
        Customer.objects.create(organisation=other, name="Theirs")

        self.assertEqual(visible_customers(SystemActor(org)).count(), 0)


class RegistryTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.today = timezone.localdate()

    def _customer(self, name, arr=100_000, **overrides):
        return Customer.objects.create(
            organisation=self.org, name=name, arr_billed_at_account=Decimal(arr), **overrides
        )

    def test_keys_are_unique_and_every_metric_names_a_known_source(self):
        keys = [m.key for m in registry.METRICS]
        self.assertEqual(len(keys), len(set(keys)))
        for metric in registry.METRICS:
            self.assertIn(metric.source, registry.SOURCES)
            self.assertIn(metric.unit, {registry.MONEY, registry.PERCENT, registry.COUNT})
            self.assertIn(metric.better, {registry.UP, registry.DOWN, registry.NONE})

    def test_values_are_the_dashboards_own_numbers(self):
        """Read through the same rollups the screens draw — never a second
        computation that can drift from them."""
        self._customer("Big", 300_000, health_score=Decimal("8.5"))
        self._customer("Small", 100_000, health_score=Decimal("3.0"))
        self._customer("Left", 50_000, churn_date=self.today - timedelta(days=30))

        values = registry.compute_all(self.org)

        self.assertEqual(values["active_customers"], 2)
        self.assertEqual(values["active_arr"], 400_000.0)
        self.assertEqual(values["average_arr"], 200_000.0)
        self.assertEqual(values["logo_retention"], 66.7)
        self.assertEqual(values["churned_arr_12m"], 50_000.0)
        self.assertEqual(values["top_three_share"], 100.0)
        self.assertEqual(values["healthy_share"], 50.0)
        self.assertEqual(values["poor_health_count"], 1)

    def test_unmeasured_stays_none_rather_than_zero(self):
        values = registry.compute_all(self.org)

        self.assertIsNone(values["logo_retention"])
        self.assertIsNone(values["nrr"])
        self.assertIsNone(values["coverage"])
        self.assertIsNone(values["healthy_share"])
        # A count of nothing is a real zero.
        self.assertEqual(values["active_customers"], 0)
        self.assertEqual(values["open_tickets"], 0)

    def test_seat_utilisation_is_seats_over_seats(self):
        # The Usage Overview's own rule: a ten-seat pilot at 100% must not
        # weigh as much as a thousand-seat rollout at 10%. A mean of the two
        # percentages would say 55.
        self._customer("Big", total_contracted_seats=1000, total_active_seats=100)
        self._customer("Small", total_contracted_seats=10, total_active_seats=10)

        values = registry.compute_all(self.org)

        self.assertEqual(values["seat_utilisation"], 10.9)
        # Shelfware is the ARR attached to the idle seats, not the whole
        # account: the big one is 90% idle, so $90K of its $100K.
        self.assertEqual(values["shelfware_arr"], 90_000.0)
        self.assertEqual(values["at_capacity_arr"], 100_000.0)

    def test_open_tickets_count_the_whole_organisation(self):
        customer = self._customer("Globex")
        for n, status in enumerate(["open", "in-progress", "resolved", "closed"]):
            Ticket.objects.create(
                customer=customer,
                ticket_number=f"TKT-{n}",
                title="t",
                assignee_name="Support",
                status=status,
                priority=Ticket.Priority.LOW,
                opened_at=self.today,
            )

        self.assertEqual(registry.compute_all(self.org)["open_tickets"], 2)

    def test_each_source_runs_once_however_many_metrics_read_it(self):
        calls = []
        original = registry.SOURCES["portfolio"]
        registry.SOURCES["portfolio"] = lambda actor: (calls.append(1), original(actor))[1]
        try:
            registry.compute_all(self.org)
        finally:
            registry.SOURCES["portfolio"] = original

        self.assertEqual(len(calls), 1)


class RecordingTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        Customer.objects.create(
            organisation=self.org, name="Globex", arr_billed_at_account=Decimal(120_000)
        )
        self.period_end = date(2026, 8, 31)

    def test_records_one_row_per_metric_for_the_period(self):
        written, had = record_period_end(self.org, self.period_end)

        self.assertEqual((written, had), (len(registry.METRICS), 0))
        row = MetricSnapshot.objects.get(
            organisation=self.org, metric="active_arr", period_end=self.period_end
        )
        self.assertEqual(row.value, Decimal("120000"))
        self.assertEqual((row.dimension, row.member), ("", ""))

    def test_an_unmeasured_metric_is_recorded_as_null_not_zero(self):
        # An organisation with no customers yet has no NRR, no retention and
        # no coverage — and a row saying 0 for any of them would be a lie
        # that the history chart then draws.
        empty = Organisation.objects.create(name="New Inc", currency="USD")
        record_period_end(empty, self.period_end)

        for key in ("nrr", "logo_retention", "coverage", "healthy_share"):
            self.assertIsNone(
                MetricSnapshot.objects.get(
                    organisation=empty, metric=key, period_end=self.period_end
                ).value,
                key,
            )
        self.assertEqual(
            MetricSnapshot.objects.get(
                organisation=empty, metric="active_customers", period_end=self.period_end
            ).value,
            Decimal("0"),
        )

    def test_a_period_already_recorded_is_kept_not_overwritten(self):
        """The row is how the month ended. Re-running later must not replace
        it with the following month's values."""
        record_period_end(self.org, self.period_end)
        MetricSnapshot.objects.filter(metric="active_arr").update(value=Decimal("1"))

        written, had = record_period_end(self.org, self.period_end)

        self.assertEqual((written, had), (0, len(registry.METRICS)))
        self.assertEqual(MetricSnapshot.objects.get(metric="active_arr").value, Decimal("1"))

    def test_dry_run_writes_nothing(self):
        written, _ = record_period_end(self.org, self.period_end, dry_run=True)

        self.assertEqual(written, len(registry.METRICS))
        self.assertFalse(MetricSnapshot.objects.exists())

    def test_a_metric_added_later_is_filled_in_without_touching_the_rest(self):
        record_period_end(self.org, self.period_end)
        MetricSnapshot.objects.filter(metric="open_tickets").delete()

        written, had = record_period_end(self.org, self.period_end)

        self.assertEqual((written, had), (1, len(registry.METRICS) - 1))
