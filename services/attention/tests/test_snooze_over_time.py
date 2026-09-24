"""A snooze survives the calendar.

Every kind is snoozed through the real endpoint (7 days, and Done), then
"today" and "now" move forward with the facts unchanged: the item stays
hidden. A day passing is not the item getting worse — only a change in the
facts (overdue, lower health, more tickets, more companies, more ARR at
stake) brings it back early. `today`/`now` are passed straight into
`rules.build_items`/`snooze.visible_items`, the two seams the list view
calls.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APIClient

from services.anomalies.models import Anomaly
from services.attention import rules, snooze
from services.customers.models import Account, Activity, Ticket

from .test_rules import POOR, AnomalyRuleTests, Fixture


class SnoozeOverTimeTests(Fixture):
    anomaly = AnomalyRuleTests.anomaly
    evidence = AnomalyRuleTests.evidence

    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.api = APIClient()
        self.api.force_authenticate(self.csm)

    # ── helpers ──────────────────────────────────────────────────────

    def snooze(self, key, **body):
        response = self.api.post(
            "/api/v1/dashboard/attention/snooze/", {"key": key, **body}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)

    def visible(self, days_later):
        today = self.today + timedelta(days=days_later)
        now = self.now + timedelta(days=days_later)
        items = rules.build_items(self.csm, {}, today=today)
        return {item["key"] for item in snooze.visible_items(self.csm, items, now=now)}

    def listed(self, key, days_later):
        """The item is still a candidate that day — so "hidden" below means
        the snooze hid it, not that it left the list on its own."""
        today = self.today + timedelta(days=days_later)
        return key in {item["key"] for item in rules.build_items(self.csm, {}, today=today)}

    def assert_survives(self, key):
        """Done stays hidden a day and a month later; 7 days stays hidden a
        day and six days later, and is back once it expires."""
        self.snooze(key, done=True)
        for days in (1, 30):
            with self.subTest(snooze="done", days_later=days):
                self.assertTrue(self.listed(key, days))
                self.assertNotIn(key, self.visible(days))

        self.snooze(key, days=7)
        for days in (1, 6):
            with self.subTest(snooze="7 days", days_later=days):
                self.assertTrue(self.listed(key, days))
                self.assertNotIn(key, self.visible(days))
        with self.subTest(snooze="7 days", days_later=8):
            self.assertIn(key, self.visible(8))

    # ── each kind survives time passing ──────────────────────────────

    def test_renewal(self):
        # 45 days out: still inside the window, and not overdue, a month on.
        customer = self.customer(
            "Soon", health_score=Decimal("5.0"), renewal_date=self.today + timedelta(days=45)
        )
        self.assert_survives(f"renewal:{customer.pk}")

    def test_overdue_renewal(self):
        customer = self.customer(
            "Late", health_score=Decimal("5.0"), renewal_date=self.today - timedelta(days=3)
        )
        self.assert_survives(f"renewal:{customer.pk}")

    def test_risk(self):
        customer = self.customer("Poor", health_score=POOR)
        self.assert_survives(f"risk:{customer.pk}")

    def test_going_quiet(self):
        customer = self.customer("Quiet", contacted=70)
        self.assert_survives(f"going_quiet:{customer.pk}")

    def test_never_contacted(self):
        customer = self.customer("Never", contacted=None)
        self.assert_survives(f"going_quiet:{customer.pk}")

    def test_support(self):
        customer = self.customer("Hot")
        self.ticket(1, customer, opened_at=self.today - timedelta(days=2))
        self.assert_survives(f"support:{customer.pk}")

    def test_anomaly(self):
        customer = self.customer("Mine")
        live = self.anomaly("Spike")
        self.evidence(live, 1, customer=customer)
        self.assert_survives(f"anomaly:{live.pk}")

    # ── each kind comes back when its facts get worse ────────────────

    def test_renewal_comes_back_once_it_goes_overdue(self):
        customer = self.customer(
            "Soon", health_score=Decimal("5.0"), renewal_date=self.today + timedelta(days=20)
        )
        key = f"renewal:{customer.pk}"
        self.snooze(key, done=True)
        self.assertNotIn(key, self.visible(19))
        # The renewal date passes: overdue is a new fact.
        self.assertIn(key, self.visible(21))

    def test_renewal_comes_back_when_health_drops(self):
        customer = self.customer(
            "Soon", health_score=Decimal("5.0"), renewal_date=self.today + timedelta(days=45)
        )
        key = f"renewal:{customer.pk}"
        self.snooze(key, done=True)
        customer.health_score = POOR
        customer.save(update_fields=["health_score"])
        self.assertIn(key, self.visible(1))

    def test_risk_comes_back_when_the_score_climbs(self):
        customer = self.customer("Poor", health_score=POOR)
        key = f"risk:{customer.pk}"
        self.snooze(key, done=True)
        # A renewal inside 90 days adds 18 points.
        customer.renewal_date = self.today + timedelta(days=60)
        customer.save(update_fields=["renewal_date"])
        self.assertIn(key, self.visible(1))

    def test_going_quiet_comes_back_when_arr_rises(self):
        customer = self.customer("Quiet", contacted=70)
        key = f"going_quiet:{customer.pk}"
        self.snooze(key, done=True)
        self.assertNotIn(key, self.visible(30))
        customer.arr_billed_at_account = 250_000
        customer.save(update_fields=["arr_billed_at_account"])
        self.assertIn(key, self.visible(30))

    def test_going_quiet_comes_back_with_a_new_silence(self):
        # A contact takes it off the list; 60 days of silence later it is a
        # new episode, and the old Done does not hide it.
        customer = self.customer("Quiet", contacted=70)
        key = f"going_quiet:{customer.pk}"
        self.snooze(key, done=True)
        Activity.objects.create(
            customer=customer, type=Activity.ActivityType.OTHER, occurred_at=self.today
        )
        self.assertFalse(self.listed(key, 1))
        self.assertTrue(self.listed(key, 60))
        self.assertIn(key, self.visible(60))

    def test_renewal_comes_back_in_its_next_cycle(self):
        # Done this cycle; the renewal is then signed and moves to next year.
        customer = self.customer(
            "Soon", health_score=Decimal("5.0"), renewal_date=self.today + timedelta(days=45)
        )
        key = f"renewal:{customer.pk}"
        self.snooze(key, done=True)
        customer.renewal_date = self.today + timedelta(days=45 + 365)
        customer.save(update_fields=["renewal_date"])
        # Out of the 90-day window until next year...
        self.assertFalse(self.listed(key, 30))
        # ...and back when it is next due, not hidden by last year's Done.
        self.assertTrue(self.listed(key, 365))
        self.assertIn(key, self.visible(365))

    def test_support_comes_back_when_one_ticket_resolves_and_another_opens(self):
        customer = self.customer("Hot")
        old = self.ticket(1, customer, opened_at=self.today - timedelta(days=2))
        key = f"support:{customer.pk}"
        self.snooze(key, done=True)
        old.status = Ticket.Status.CLOSED
        old.save(update_fields=["status"])
        self.ticket(2, customer)
        # Still one open ticket, but not the one that was dealt with.
        self.assertIn(key, self.visible(3))

    def test_support_comes_back_with_another_ticket(self):
        customer = self.customer("Hot")
        self.ticket(1, customer, opened_at=self.today - timedelta(days=2))
        key = f"support:{customer.pk}"
        self.snooze(key, done=True)
        self.assertNotIn(key, self.visible(10))
        self.ticket(2, customer, priority=Ticket.Priority.CRITICAL)
        self.assertIn(key, self.visible(10))

    def test_anomaly_comes_back_when_it_spreads(self):
        customer = self.customer("Mine")
        also = self.customer("Also mine")
        live = self.anomaly("Spike")
        self.evidence(live, 1, customer=customer)
        key = f"anomaly:{live.pk}"
        self.snooze(key, done=True)
        self.assertNotIn(key, self.visible(5))
        account = Account.objects.create(name="Division", owner=self.csm)
        account.customers.add(also)
        self.evidence(live, 2, account=account)
        self.assertIn(key, self.visible(5))
        self.assertEqual(live.status, Anomaly.Status.LIVE)
