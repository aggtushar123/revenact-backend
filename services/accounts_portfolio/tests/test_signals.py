from datetime import timedelta
from decimal import Decimal

from services.accounts_portfolio import book
from services.accounts_portfolio.params import parse_params
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.customers.contact import last_contact_by_account
from services.customers.models import Activity, HealthSnapshot, Ticket
from services.customers.triage import triage


class AccountSignalTests(AccountPortfolioFixture):
    def entry(self, account, user=None, **query):
        portfolio = book.load_portfolio(user or self.csm, parse_params(query), today=self.today)
        return next(e for e in portfolio.entries if e.account.pk == account.pk)

    def snapshot(self, account, months, score):
        return HealthSnapshot.objects.create(
            account=account,
            captured_on=self.today - timedelta(days=30 * months),
            health_score=Decimal(score),
        )

    def ticket(self, number, *, account=None, customer=None, **fields):
        values = {
            "status": Ticket.Status.OPEN,
            "priority": Ticket.Priority.HIGH,
            "opened_at": self.today,
            **fields,
        }
        return Ticket.objects.create(
            account=account, customer=customer, ticket_number=number, title="Down", **values
        )

    def test_arr_is_the_stored_figure(self):
        account = self.account("Plain", arr=Decimal("69600.50"))
        self.assertEqual(self.entry(account).arr, 69600.5)

    def test_trend_is_the_accounts_snapshots_then_todays_score(self):
        account = self.account("Falling", health_score=Decimal("4.9"))
        for months, score in (
            (8, "9.0"),
            (5, "6.2"),
            (4, "5.8"),
            (3, "5.5"),
            (2, "5.1"),
            (1, "5.0"),
        ):
            self.snapshot(account, months, score)
        # The organisation's own snapshots are not the account's.
        HealthSnapshot.objects.create(
            customer=self.pizza,
            captured_on=self.today - timedelta(days=30),
            health_score=Decimal("1.0"),
        )
        self.assertEqual(self.entry(account).trend, [6.2, 5.8, 5.5, 5.1, 5.0, 4.9])

    def test_risk_is_triage_over_the_twelve_month_history(self):
        account = self.account(
            "Slipping",
            health_score=Decimal("3.0"),
            csm_pulse_score=4,
            ai_pulse_value=2,
            renewal_date=self.today + timedelta(days=60),
        )
        for months, score in ((3, "8.0"), (2, "5.0"), (1, "3.0")):
            self.snapshot(account, months, score)
        expected = triage(
            health_category="poor",
            csm_pulse=4,
            ai_pulse=2,
            renewal_date=account.renewal_date,
            history=["good", "average", "poor"],
            today=self.today,
        )
        entry = self.entry(account)
        self.assertEqual(entry.triage, expected)
        self.assertEqual(entry.signal, {"kind": "risk", "label": f"Risk {expected.score}"})

    def test_overdue_renewal_beats_risk(self):
        account = self.account(
            "Late", health_score=Decimal("2.0"), renewal_date=self.today - timedelta(days=1)
        )
        entry = self.entry(account)
        self.assertEqual(entry.renewal_days, -1)
        self.assertEqual(entry.signal, {"kind": "renewal_overdue", "label": "Renewal overdue"})

    def test_a_churn_stage_account_still_signals(self):
        """No churn on accounts: the stage is only a stage, so a late renewal
        is still called out (Organizations would say nothing for a churned
        customer)."""
        account = self.account(
            "Stage only", lifecycle_stage="churn", renewal_date=self.today - timedelta(days=3)
        )
        self.assertEqual(self.entry(account).signal["kind"], "renewal_overdue")

    def test_last_touch_is_activity_trackings_account_rule(self):
        touched = self.account("Touched")
        silent = self.account("Silent")
        Activity.objects.create(
            account=touched,
            type=Activity.ActivityType.OTHER,
            occurred_at=self.today - timedelta(days=4),
        )
        # Contact logged on the organisation is the organisation's.
        Activity.objects.create(
            customer=self.pizza, type=Activity.ActivityType.OTHER, occurred_at=self.today
        )
        self.assertEqual(self.entry(touched).last_touch_days, 4)
        self.assertEqual(
            last_contact_by_account([touched.pk]), {touched.pk: self.today - timedelta(days=4)}
        )
        self.assertIsNone(self.entry(silent).last_touch_days)

    def test_urgent_tickets_are_the_accounts_own_under_the_department_rule(self):
        account = self.account("Busy")
        self.ticket("T-1", account=account)
        self.ticket("T-2", account=account, priority=Ticket.Priority.CRITICAL)
        self.ticket("T-3", account=account, priority=Ticket.Priority.LOW)
        self.ticket("T-4", account=account, status="resolved")
        self.ticket("T-5", account=account, department="engineering")
        self.ticket("T-6", customer=self.pizza)
        entry = self.entry(account)
        # Carl works in CS: the engineering ticket is not his to read.
        self.assertEqual(entry.urgent_tickets, 2)
        self.assertEqual(entry.signal, {"kind": "tickets", "label": "2 open tickets"})
        # Leadership reads every department.
        self.assertEqual(self.entry(account, user=self.admin).urgent_tickets, 3)

    def test_linked_organisations_name_only_what_the_viewer_may_open(self):
        pool = self.account("Pool", customers=[self.taco, self.pizza], owner=None)
        self.assertEqual(self.entry(pool).organisations, [(self.pizza.pk, "Pizza Hut")])
        self.assertEqual(
            self.entry(pool, user=self.admin).organisations,
            [(self.pizza.pk, "Pizza Hut"), (self.taco.pk, "Taco Bell")],
        )
        lone = self.account("Lone", customers=[self.taco], owner=None)
        self.assertEqual(self.entry(lone).organisations, [])

    def test_the_portfolio_carries_the_tenant(self):
        portfolio = book.load_portfolio(self.csm, parse_params({}), today=self.today)
        self.assertEqual(portfolio.organisation, self.org)
