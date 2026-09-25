from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from services.accounts.models import User
from services.attention import rules as attention_rules
from services.customers.activity_tracking import gone_quiet_values
from services.customers.contact import last_contact_by_customer
from services.customers.models import Account, Activity, Customer, HealthSnapshot, Ticket
from services.fx_rates.models import FxRate
from services.organizations import book
from services.organizations.params import parse_params
from services.organizations.tests.fixtures import PortfolioFixture


class SignalPriorityTests(SimpleTestCase):
    def test_overdue_beats_risk_beats_tickets(self):
        signal = book.signal_for
        self.assertEqual(
            signal(churned=False, renewal_days=-1, risk=90, urgent_tickets=3),
            {"kind": "renewal_overdue", "label": "Renewal overdue"},
        )
        self.assertEqual(
            signal(churned=False, renewal_days=0, risk=40, urgent_tickets=3),
            {"kind": "risk", "label": "Risk 40"},
        )
        self.assertEqual(
            signal(churned=False, renewal_days=None, risk=39, urgent_tickets=1),
            {"kind": "tickets", "label": "1 open ticket"},
        )
        self.assertEqual(
            signal(churned=False, renewal_days=None, risk=0, urgent_tickets=2)["label"],
            "2 open tickets",
        )

    def test_nothing_or_churned_is_no_signal(self):
        self.assertIsNone(book.signal_for(churned=False, renewal_days=5, risk=39, urgent_tickets=0))
        self.assertIsNone(
            book.signal_for(churned=True, renewal_days=-30, risk=90, urgent_tickets=4)
        )


class DashboardEqualityTests(PortfolioFixture):
    """Each row signal equals what the dashboard shows for the same account."""

    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.csm)

    def entry(self, customer, user=None, **query):
        portfolio = book.load_portfolio(user or self.csm, parse_params(query), today=self.today)
        return next(e for e in portfolio.entries if e.customer.pk == customer.pk)

    def health_row(self, customer, **query):
        response = self.api.get("/api/v1/customers/health/", query)
        self.assertEqual(response.status_code, 200)
        return next(row for row in response.data["results"] if row["id"] == customer.pk)

    def test_health_trend_and_triage_equal_the_health_overview(self):
        pizza = self.customer(
            "Pizza Hut",
            health_score=Decimal("4.9"),
            csm_pulse_score=4,
            ai_pulse_value=1,
            renewal_date=self.today + timedelta(days=40),
        )
        for months_ago, score in ((8, "7.5"), (5, "6.2"), (4, "5.8"), (3, "5.5"), (2, "5.1")):
            HealthSnapshot.objects.create(
                customer=pizza,
                captured_on=self.today - timedelta(days=30 * months_ago),
                health_score=Decimal(score),
            )
        HealthSnapshot.objects.create(
            customer=pizza, captured_on=self.today - timedelta(days=30), health_score=Decimal("5.0")
        )

        entry = self.entry(pizza)
        dashboard = self.health_row(pizza)
        six_months = self.health_row(pizza, history_months="6")

        self.assertEqual(float(entry.customer.health_score), float(dashboard["health_score"]))
        self.assertEqual(entry.customer.health_category, dashboard["health_category"])
        # The trend is the last five month-end snapshots in the six-month window,
        # then today's score, so the line ends at the ring's number.
        history = [float(point["health_score"]) for point in six_months["history"]]
        self.assertEqual(entry.trend, history[-5:] + [float(dashboard["health_score"])])
        self.assertEqual(entry.trend, [6.2, 5.8, 5.5, 5.1, 5.0, 4.9])
        # Triage reads the Health view's own (12-month) history.
        self.assertEqual(entry.triage.score, dashboard["triage_score"])
        self.assertEqual(entry.triage.direction, dashboard["triage_direction"])
        # Average 22 + AI three below the CSM 33 + renews in 40 days 18.
        self.assertEqual(entry.triage.score, 73)

    def test_a_customer_with_no_snapshots_has_a_one_point_trend(self):
        fresh = self.customer("Fresh", health_score=Decimal("6.0"))
        self.assertEqual(self.entry(fresh).trend, [6.0])

    def test_arr_is_converted_like_the_dashboard(self):
        FxRate.objects.create(
            organisation=self.org, currency="EUR", rate_to_org_currency=Decimal("1.1")
        )
        euro = self.customer("Euro", currency="EUR", arr_billed_at_account=Decimal("60000"))
        pound = self.customer("Pound", currency="GBP", arr_billed_at_account=Decimal("10000"))
        dollar = self.customer("Dollar", arr_billed_at_account=Decimal("12345.67"))
        for customer in (euro, pound, dollar):
            self.assertEqual(self.entry(customer).arr, self.health_row(customer)["arr"])
        self.assertEqual(self.entry(euro).arr, 66000.0)
        self.assertIsNone(self.entry(pound).arr)

    def test_last_touch_is_activity_trackings_rule(self):
        quiet = self.customer("Quiet")
        never = self.customer("Never")
        recent = self.customer("Recent")
        Activity.objects.create(
            customer=quiet,
            type=Activity.ActivityType.OTHER,
            occurred_at=self.today - timedelta(days=70),
        )
        Activity.objects.create(
            customer=recent,
            type=Activity.ActivityType.OTHER,
            occurred_at=self.today - timedelta(days=3),
        )
        dark = gone_quiet_values(self.csm, {})
        self.assertEqual(self.entry(quiet).last_touch_days, dark[quiet.pk])
        self.assertEqual(self.entry(quiet).last_touch_days, 70)
        self.assertIsNone(self.entry(never).last_touch_days)
        self.assertIsNone(dark[never.pk])
        latest = last_contact_by_customer([recent.pk])[recent.pk]
        self.assertEqual(self.entry(recent).last_touch_days, (self.today - latest).days)

    def ticket(self, number, **fields):
        values = {
            "ticket_number": f"TKT-{number}",
            "title": "Down",
            "status": Ticket.Status.OPEN,
            "priority": Ticket.Priority.HIGH,
            "opened_at": self.today,
            **fields,
        }
        return Ticket.objects.create(**values)

    def test_urgent_tickets_equal_the_attention_list(self):
        acme = self.customer("Acme")
        division = Account.objects.create(name="Acme EMEA")
        division.customers.add(acme)
        self.ticket(1, customer=acme)
        self.ticket(2, customer=acme, priority=Ticket.Priority.CRITICAL)
        self.ticket(3, account=division)
        self.ticket(4, customer=acme, priority=Ticket.Priority.MEDIUM)
        self.ticket(5, customer=acme, status=Ticket.Status.RESOLVED)
        # Another department's ticket: Carl (Customer Success) cannot read it.
        self.ticket(6, customer=acme, department="engineering")

        entry = self.entry(acme)
        items = attention_rules.build_items(self.csm, {}, today=self.today)
        support = next(item for item in items if item["key"] == f"support:{acme.pk}")
        self.assertEqual(entry.urgent_tickets, support["fingerprint"]["count"])
        self.assertEqual(entry.urgent_tickets, 3)
        self.assertEqual(entry.signal, {"kind": "tickets", "label": "3 open tickets"})

    def test_signals_on_real_rows(self):
        overdue = self.customer(
            "Overdue", health_score=Decimal("2.0"), renewal_date=self.today - timedelta(days=5)
        )
        risky = self.customer("Risky", health_score=Decimal("2.0"))
        calm = self.customer("Calm")
        left = self.customer(
            "Left",
            lifecycle_stage="churn",
            churn_date=self.today - timedelta(days=9),
            renewal_date=self.today - timedelta(days=30),
        )
        self.assertEqual(self.entry(overdue).signal["kind"], "renewal_overdue")
        self.assertEqual(self.entry(overdue).renewal_days, -5)
        self.assertEqual(self.entry(risky).signal, {"kind": "risk", "label": "Risk 66"})
        self.assertIsNone(self.entry(calm).signal)
        churned = self.entry(left, include_churned="1")
        self.assertTrue(churned.churned)
        self.assertIsNone(churned.signal)


class QueryCountTests(PortfolioFixture):
    """The load reads the whole book in a fixed number of queries, however
    many rows, snapshots, touches and tickets it carries."""

    def populate(self, count):
        for index in range(count):
            customer = self.customer(f"Row {Customer.objects.count()}")
            HealthSnapshot.objects.create(
                customer=customer,
                captured_on=self.today - timedelta(days=30),
                health_score=Decimal("6.0"),
            )
            Activity.objects.create(
                customer=customer,
                type=Activity.ActivityType.OTHER,
                occurred_at=self.today - timedelta(days=index + 1),
            )
            division = Account.objects.create(name=f"Division {customer.pk}")
            division.customers.add(customer)
            Ticket.objects.create(
                ticket_number=f"TKT-{customer.pk}",
                title="Down",
                status=Ticket.Status.OPEN,
                priority=Ticket.Priority.HIGH,
                opened_at=self.today,
                account=division,
            )

    def queries(self):
        # A freshly loaded user, as each request has: nothing cached from the
        # previous call (the membership lookup is memoised on the instance).
        user = User.objects.get(pk=self.csm.pk)
        with CaptureQueriesContext(connection) as captured:
            portfolio = book.load_portfolio(user, parse_params({}), today=self.today)
            for entry in portfolio.entries:
                # Everything a row reads, so a lazy relation would show up here.
                customer = entry.customer
                (customer.owner, customer.primary_product, customer.health_category)
        return len(portfolio.entries), len(captured)

    def test_the_query_count_does_not_grow_with_the_book(self):
        self.populate(1)
        rows, small = self.queries()
        self.assertEqual(rows, 1)
        self.populate(6)
        rows, large = self.queries()
        self.assertEqual(rows, 7)
        self.assertEqual(small, large)
