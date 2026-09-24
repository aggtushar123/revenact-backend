from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from services.accounts.models import Organisation, User
from services.anomalies.models import Anomaly, AnomalyEvidence
from services.attention import rules
from services.customers.models import Account, Activity, Customer, HealthSnapshot, Ticket
from services.fx_rates.models import FxRate


class UrgencyTests(SimpleTestCase):
    def test_days_to_renewal(self):
        self.assertEqual(rules.urgency_for_days_to(None), 0.5)
        self.assertEqual(rules.urgency_for_days_to(-5), 1.0)
        self.assertEqual(rules.urgency_for_days_to(0), 1.0)
        self.assertEqual(rules.urgency_for_days_to(14), 1.0)
        self.assertEqual(rules.urgency_for_days_to(52), 0.625)
        self.assertEqual(rules.urgency_for_days_to(90), 0.25)
        self.assertEqual(rules.urgency_for_days_to(120), 0.25)
        # Linear in between, rounded to three places: 1 - 0.75 * 1/76.
        self.assertEqual(rules.urgency_for_days_to(15), 0.99)

    def test_quiet(self):
        self.assertEqual(rules.urgency_quiet(None), 1.0)
        self.assertEqual(rules.urgency_quiet(60), 0.25)
        self.assertEqual(rules.urgency_quiet(90), 0.625)
        self.assertEqual(rules.urgency_quiet(120), 1.0)
        self.assertEqual(rules.urgency_quiet(400), 1.0)

    def test_ticket_age(self):
        self.assertEqual(rules.urgency_ticket_age(0), 0.25)
        self.assertEqual(rules.urgency_ticket_age(7), 0.625)
        self.assertEqual(rules.urgency_ticket_age(14), 1.0)
        self.assertEqual(rules.urgency_ticket_age(30), 1.0)
        # 0.25 + 0.75 / 14, rounded to three places.
        self.assertEqual(rules.urgency_ticket_age(1), 0.304)

    def test_anomaly_age(self):
        self.assertEqual(rules.urgency_anomaly_age(0), 1.0)
        self.assertEqual(rules.urgency_anomaly_age(7), 1.0)
        self.assertEqual(rules.urgency_anomaly_age(90), 0.25)
        self.assertEqual(rules.urgency_anomaly_age(200), 0.25)
        # 1 - 0.75 * 41.5/83 at the midpoint; 48 days is 1 - 0.75 * 41/83.
        self.assertEqual(rules.urgency_anomaly_age(48), 0.63)


class WorseTests(SimpleTestCase):
    def test_renewal(self):
        stored = {"days": 30, "health": "average", "arr": 100.0}
        self.assertFalse(rules.worse("renewal", stored, dict(stored)))
        self.assertFalse(rules.worse("renewal", stored, {**stored, "days": 40}))
        self.assertFalse(rules.worse("renewal", stored, {**stored, "health": "good"}))
        self.assertFalse(rules.worse("renewal", stored, {**stored, "arr": 50.0}))
        self.assertTrue(rules.worse("renewal", stored, {**stored, "days": 29}))
        self.assertTrue(rules.worse("renewal", stored, {**stored, "days": -3}))
        self.assertTrue(rules.worse("renewal", stored, {**stored, "health": "poor"}))
        self.assertTrue(rules.worse("renewal", stored, {**stored, "arr": 101.0}))

    def test_risk(self):
        stored = {"score": 50, "arr": 100.0}
        self.assertFalse(rules.worse("risk", stored, dict(stored)))
        self.assertFalse(rules.worse("risk", stored, {"score": 45, "arr": 90.0}))
        self.assertTrue(rules.worse("risk", stored, {**stored, "score": 51}))
        self.assertTrue(rules.worse("risk", stored, {**stored, "arr": 100.5}))

    def test_going_quiet(self):
        stored = {"days": 70, "arr": 100.0}
        self.assertFalse(rules.worse("going_quiet", stored, dict(stored)))
        self.assertFalse(rules.worse("going_quiet", stored, {**stored, "days": 65}))
        self.assertTrue(rules.worse("going_quiet", stored, {**stored, "days": 71}))
        self.assertTrue(rules.worse("going_quiet", stored, {**stored, "arr": 200.0}))
        # -1 means never contacted, which is the worst silence of all.
        self.assertTrue(rules.worse("going_quiet", stored, {**stored, "days": -1}))
        never = {"days": -1, "arr": 100.0}
        self.assertFalse(rules.worse("going_quiet", never, dict(never)))
        self.assertFalse(rules.worse("going_quiet", never, {**never, "days": 500}))

    def test_support(self):
        stored = {"count": 2, "oldest": 5, "arr": 100.0}
        self.assertFalse(rules.worse("support", stored, dict(stored)))
        self.assertFalse(rules.worse("support", stored, {"count": 1, "oldest": 3, "arr": 100.0}))
        self.assertTrue(rules.worse("support", stored, {**stored, "count": 3}))
        self.assertTrue(rules.worse("support", stored, {**stored, "oldest": 6}))
        self.assertTrue(rules.worse("support", stored, {**stored, "arr": 150.0}))

    def test_anomaly(self):
        stored = {"companies": 2, "arr": 100.0}
        self.assertFalse(rules.worse("anomaly", stored, dict(stored)))
        self.assertFalse(rules.worse("anomaly", stored, {"companies": 1, "arr": 50.0}))
        self.assertTrue(rules.worse("anomaly", stored, {**stored, "companies": 3}))
        self.assertTrue(rules.worse("anomaly", stored, {**stored, "arr": 100.01}))

    def test_unknown_kind_is_never_worse(self):
        self.assertFalse(rules.worse("nope", {"arr": 1}, {"arr": 2}))


GOOD, AVERAGE, POOR = Decimal("8.0"), Decimal("5.0"), Decimal("2.0")


class Fixture(TestCase):
    """Every customer is Good, contacted today and without a renewal date
    unless a test says otherwise, so each test's items come only from the
    rule it is about."""

    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )

    def customer(self, name, *, owner=None, contacted=0, **fields):
        customer = Customer.objects.create(
            organisation=self.org,
            name=name,
            owner=owner or self.csm,
            health_score=fields.pop("health_score", GOOD),
            arr_billed_at_account=fields.pop("arr", 100_000),
            currency=fields.pop("currency", "USD"),
            **fields,
        )
        if contacted is not None:
            Activity.objects.create(
                customer=customer,
                type=Activity.ActivityType.OTHER,
                occurred_at=self.today - timedelta(days=contacted),
            )
        return customer

    def ticket(self, n, customer, **overrides):
        return Ticket.objects.create(
            **{
                "customer": customer,
                "ticket_number": f"TKT-{n}",
                "title": "Secret complaint text",
                "status": Ticket.Status.OPEN,
                "priority": Ticket.Priority.HIGH,
                "opened_at": self.today,
                **overrides,
            }
        )

    def items(self, params=None, user=None):
        return rules.build_items(user or self.csm, params or {}, today=self.today)

    def keys(self, kind=None, params=None, user=None):
        return {
            item["key"] for item in self.items(params, user) if kind is None or item["kind"] == kind
        }

    def item(self, key, params=None):
        return next(item for item in self.items(params) if item["key"] == key)


class RenewalRuleTests(Fixture):
    def test_inside_90_days_and_not_good(self):
        soon = self.customer(
            "Soon", health_score=POOR, renewal_date=self.today + timedelta(days=30)
        )
        edge = self.customer(
            "Edge", health_score=AVERAGE, renewal_date=self.today + timedelta(days=90)
        )
        overdue = self.customer(
            "Overdue", health_score=AVERAGE, renewal_date=self.today - timedelta(days=5)
        )
        # Near misses: one day too far out; Good health; no renewal date at all.
        self.customer("Far", health_score=AVERAGE, renewal_date=self.today + timedelta(days=91))
        self.customer("Fine", health_score=GOOD, renewal_date=self.today + timedelta(days=10))
        self.customer("Undated", health_score=POOR)

        self.assertEqual(
            self.keys("renewal"),
            {f"renewal:{soon.pk}", f"renewal:{edge.pk}", f"renewal:{overdue.pk}"},
        )

    def test_shape_reason_and_score(self):
        soon = self.customer(
            "Soon", health_score=POOR, renewal_date=self.today + timedelta(days=52)
        )
        overdue = self.customer(
            "Overdue",
            health_score=AVERAGE,
            renewal_date=self.today - timedelta(days=5),
            arr=40_000,
        )

        item = self.item(f"renewal:{soon.pk}")
        self.assertEqual(
            item,
            {
                "key": f"renewal:{soon.pk}",
                "kind": "renewal",
                "title": "Soon",
                "reason": "renews in 52 days · health Poor",
                "at_stake": 100000.0,
                "urgency": 0.625,
                "score": 62500.0,
                "customer_id": soon.pk,
                "companies": [],
                "fingerprint": {"days": 52, "health": "poor", "arr": 100000.0},
            },
        )
        late = self.item(f"renewal:{overdue.pk}")
        self.assertEqual(late["reason"], "renewal 5 days overdue · health Average")
        self.assertEqual(late["urgency"], 1.0)
        self.assertEqual(late["score"], 40000.0)
        self.assertEqual(late["fingerprint"], {"days": -5, "health": "average", "arr": 40000.0})


class RiskRuleTests(Fixture):
    def test_at_or_above_the_action_threshold(self):
        # Average (22) + renewal inside 90 days (18) = 40: exactly the threshold.
        at = self.customer("At", health_score=AVERAGE, renewal_date=self.today + timedelta(days=60))
        # Poor (66) with no renewal date at all.
        poor = self.customer("Poor", health_score=POOR)
        # Near miss: Average (22) + renewal 91-180 days (8) = 30.
        self.customer("Below", health_score=AVERAGE, renewal_date=self.today + timedelta(days=120))

        self.assertEqual(self.keys("risk"), {f"risk:{at.pk}", f"risk:{poor.pk}"})

        undated = self.item(f"risk:{poor.pk}")
        self.assertEqual(undated["reason"], "risk 66 · Health is Poor")
        self.assertEqual(undated["urgency"], 0.5)
        self.assertEqual(undated["score"], 50000.0)
        self.assertEqual(undated["fingerprint"], {"score": 66, "arr": 100000.0})
        self.assertEqual(undated["customer_id"], poor.pk)
        self.assertEqual(undated["companies"], [])

    def test_score_equals_the_health_serializers_triage_score(self):
        customer = self.customer(
            "Sliding",
            health_score=POOR,
            csm_pulse_score=4,
            ai_pulse_value=2,
            renewal_date=self.today + timedelta(days=100),
        )
        # A fall from Good to Poor over the last three months adds a decline
        # factor, so the history path has to be the serializer's for these
        # two numbers to agree.
        for months_ago, score in ((3, "8.0"), (2, "5.0"), (1, "2.0")):
            HealthSnapshot.objects.create(
                customer=customer,
                captured_on=self.today - timedelta(days=31 * months_ago),
                health_score=Decimal(score),
            )

        client = APIClient()
        client.force_authenticate(self.csm)
        rows = client.get("/api/v1/customers/health/").data["results"]
        expected = next(row for row in rows if row["id"] == customer.pk)["triage_score"]

        item = self.item(f"risk:{customer.pk}")
        self.assertEqual(item["fingerprint"]["score"], expected)
        # Poor 66 + pulse gap 22 + renewal-near 8 + decline 27.
        self.assertEqual(expected, 123)
        self.assertEqual(item["reason"], "risk 123 · Health is Poor")
        # 100 days out is past the 90-day window: the floor.
        self.assertEqual(item["urgency"], 0.25)


class GoingQuietRuleTests(Fixture):
    def test_sixty_days_or_never(self):
        quiet = self.customer("Quiet", contacted=60)
        never = self.customer("Never", contacted=None)
        # Near miss: contacted 59 days ago.
        self.customer("Recent", contacted=59)

        self.assertEqual(
            self.keys("going_quiet"), {f"going_quiet:{quiet.pk}", f"going_quiet:{never.pk}"}
        )

        item = self.item(f"going_quiet:{quiet.pk}")
        self.assertEqual(item["reason"], "no contact in 60 days")
        self.assertEqual(item["urgency"], 0.25)
        self.assertEqual(item["score"], 25000.0)
        self.assertEqual(item["fingerprint"], {"days": 60, "arr": 100000.0})

        silent = self.item(f"going_quiet:{never.pk}")
        self.assertEqual(silent["reason"], "never contacted")
        self.assertEqual(silent["urgency"], 1.0)
        self.assertEqual(silent["fingerprint"], {"days": -1, "arr": 100000.0})


class SupportRuleTests(Fixture):
    def test_open_high_and_critical_tickets_only(self):
        hot = self.customer("Hot")
        medium = self.customer("Medium")
        resolved = self.customer("Resolved")
        engineering = self.customer("Engineering")
        self.ticket(1, hot, priority=Ticket.Priority.CRITICAL, opened_at=self.today - timedelta(7))
        self.ticket(2, hot, opened_at=self.today - timedelta(days=2))
        self.ticket(3, hot, status=Ticket.Status.CLOSED)
        # Near misses: medium priority; resolved; another department's ticket.
        self.ticket(4, medium, priority=Ticket.Priority.MEDIUM)
        self.ticket(5, resolved, priority=Ticket.Priority.CRITICAL, status="resolved")
        self.ticket(6, engineering, department=User.Function.ENGINEERING)

        self.assertEqual(self.keys("support"), {f"support:{hot.pk}"})

        item = self.item(f"support:{hot.pk}")
        self.assertEqual(item["reason"], "2 open High/Critical tickets · oldest 7 days")
        self.assertEqual(item["urgency"], 0.625)
        self.assertEqual(item["score"], 62500.0)
        self.assertEqual(item["fingerprint"], {"count": 2, "oldest": 7, "arr": 100000.0})
        self.assertNotIn("Secret", item["reason"] + item["title"])

    def test_an_account_ticket_counts_for_each_of_its_customers_in_the_set(self):
        first = self.customer("First")
        second = self.customer("Second")
        theirs = self.customer("Theirs", owner=self.other)
        # Owned by Dana: an account Carl owned would put Theirs in his book.
        account = Account.objects.create(name="Shared", owner=self.other)
        account.customers.add(first, second, theirs)
        self.ticket(1, None, account=account)

        self.assertEqual(self.keys("support"), {f"support:{first.pk}", f"support:{second.pk}"})
        self.assertEqual(
            self.keys("support", params={"customer": str(first.pk)}), {f"support:{first.pk}"}
        )
        self.assertEqual(
            self.item(f"support:{first.pk}")["reason"],
            "1 open High/Critical ticket · oldest 0 days",
        )


class AnomalyRuleTests(Fixture):
    def anomaly(self, title, *, status=Anomaly.Status.LIVE, first_seen_days=3):
        seen = timezone.now() - timedelta(days=first_seen_days)
        return Anomaly.objects.create(
            organisation=self.org,
            title=title,
            status=status,
            first_seen_at=seen,
            last_seen_at=timezone.now(),
        )

    def evidence(self, anomaly, n, *, customer=None, account=None, **fields):
        return AnomalyEvidence.objects.create(
            anomaly=anomaly,
            organisation=self.org,
            kind=fields.pop("kind", AnomalyEvidence.Kind.CALL),
            record_id=n,
            customer=customer,
            account=account,
            snippet="Record text that must never leak",
            occurred_at=timezone.now(),
            **fields,
        )

    def test_live_with_evidence_on_a_visible_company(self):
        mine = self.customer("Mine", arr=60_000)
        also = self.customer("Also mine", arr=40_000)
        theirs = self.customer("Theirs", owner=self.other)

        live = self.anomaly("SSO login failures", first_seen_days=48)
        self.evidence(live, 1, customer=mine)
        account = Account.objects.create(name="Division", owner=self.csm)
        account.customers.add(also)
        self.evidence(live, 2, account=account)
        self.evidence(live, 3, customer=theirs)

        # Near misses: acknowledged; evidence only on another CSM's customer;
        # evidence only in a ticket from another department.
        acked = self.anomaly("Old news", status=Anomaly.Status.ACKNOWLEDGED)
        self.evidence(acked, 4, customer=mine)
        hidden = self.anomaly("Not yours")
        self.evidence(hidden, 5, customer=theirs)
        unreadable = self.anomaly("Engineering only")
        self.evidence(
            unreadable,
            6,
            customer=mine,
            kind=AnomalyEvidence.Kind.TICKET,
            department=User.Function.ENGINEERING,
        )

        self.assertEqual(self.keys("anomaly"), {f"anomaly:{live.pk}"})

        item = self.item(f"anomaly:{live.pk}")
        self.assertEqual(
            item,
            {
                "key": f"anomaly:{live.pk}",
                "kind": "anomaly",
                "title": "SSO login failures",
                "reason": "2 companies · first seen 48 days ago",
                "at_stake": 100000.0,
                "urgency": 0.63,
                "score": 63000.0,
                "customer_id": None,
                "companies": [
                    {"id": also.pk, "name": "Also mine"},
                    {"id": mine.pk, "name": "Mine"},
                ],
                "fingerprint": {"companies": 2, "arr": 100000.0},
            },
        )

    def test_filters_narrow_the_companies(self):
        mine = self.customer("Mine")
        also = self.customer("Also mine")
        live = self.anomaly("Spike")
        self.evidence(live, 1, customer=mine)
        self.evidence(live, 2, customer=also)

        item = self.item(f"anomaly:{live.pk}", params={"customer": str(mine.pk)})
        self.assertEqual(item["companies"], [{"id": mine.pk, "name": "Mine"}])
        self.assertEqual(item["reason"], "1 company · first seen 3 days ago")


class MoneyTests(Fixture):
    def test_arr_is_converted_to_the_org_currency(self):
        FxRate.objects.create(
            organisation=self.org, currency="EUR", rate_to_org_currency=Decimal("1.1")
        )
        euro = self.customer("Euro", contacted=None, arr=100_000, currency="EUR")

        item = self.item(f"going_quiet:{euro.pk}")
        self.assertEqual(item["at_stake"], 110000.0)
        self.assertEqual(item["score"], 110000.0)
        self.assertEqual(item["reason"], "never contacted")

    def test_unconvertible_arr_is_zero_at_stake_and_says_so(self):
        pound = self.customer("Pound", contacted=None, arr=100_000, currency="GBP")

        item = self.item(f"going_quiet:{pound.pk}")
        self.assertEqual(item["at_stake"], 0.0)
        self.assertEqual(item["score"], 0.0)
        self.assertEqual(item["reason"], "never contacted · ARR unknown")


class ScopingAndFilterTests(Fixture):
    def test_another_csms_customer_never_appears(self):
        theirs = self.customer(
            "Theirs",
            owner=self.other,
            contacted=None,
            health_score=POOR,
            renewal_date=self.today + timedelta(days=5),
        )
        self.ticket(1, theirs)

        self.assertEqual(self.keys(), set())
        self.assertEqual(
            self.keys(user=self.other),
            {
                f"renewal:{theirs.pk}",
                f"risk:{theirs.pk}",
                f"going_quiet:{theirs.pk}",
                f"support:{theirs.pk}",
            },
        )

    def test_owner_lifecycle_and_customer_filters_narrow_items(self):
        manager = User.objects.create_user(
            email="boss@acme.io",
            password="supersecret1",
            name="Boss",
            organisation=self.org,
            role=User.Role.ADMIN,
            function=User.Function.CS,
        )
        mine = self.customer(
            "Mine", contacted=None, lifecycle_stage=Customer.LifecycleStage.ONBOARDING
        )
        theirs = self.customer(
            "Theirs",
            owner=self.other,
            contacted=None,
            lifecycle_stage=Customer.LifecycleStage.ADOPTION,
        )
        both = {f"going_quiet:{mine.pk}", f"going_quiet:{theirs.pk}"}

        self.assertEqual(self.keys(user=manager), both)
        self.assertEqual(
            self.keys(user=manager, params={"owner": str(self.other.pk)}),
            {f"going_quiet:{theirs.pk}"},
        )
        self.assertEqual(
            self.keys(user=manager, params={"lifecycle": Customer.LifecycleStage.ONBOARDING}),
            {f"going_quiet:{mine.pk}"},
        )
        self.assertEqual(
            self.keys(user=manager, params={"customer": str(theirs.pk)}),
            {f"going_quiet:{theirs.pk}"},
        )
        self.assertEqual(self.keys(user=manager, params={"owner": "unassigned"}), set())
        # Bad values are ignored, never an error.
        self.assertEqual(
            self.keys(user=manager, params={"owner": "x", "lifecycle": "nope", "customer": "?"}),
            both,
        )
