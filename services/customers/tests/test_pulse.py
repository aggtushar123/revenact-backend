"""Account pulse: five signals blended into how the relationship feels."""

from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers import pulse
from services.customers.models import Account, Call, Customer, Email, Ticket


def compute(**overrides):
    inputs = {
        "ai_pulse_value": None,
        "csm_pulse_score": None,
        "csm_pulse_age_days": None,
        "positive_count": 0,
        "negative_count": 0,
        "classified_count": 0,
        "days_since_touch": None,
        "open_ticket_count": 0,
    }
    inputs.update(overrides)
    return pulse.compute(**inputs)


class PulseRubricTests(APITestCase):
    def test_every_signal_measured_blends_by_weight(self):
        result = compute(
            ai_pulse_value=4,
            csm_pulse_score=3,
            csm_pulse_age_days=5,
            positive_count=3,
            negative_count=1,
            classified_count=4,
            days_since_touch=9,
            open_ticket_count=2,
        )
        # Readings: AI 4.0, CSM 3.0, sentiment ((3-1)/4+1)/2=0.75→4.0,
        # touch 1-9/90=0.9→4.6, support 1-2/10=0.8→4.2; weighted mean of the
        # 0..1 ratios: (3*.75+2.5*.5+2*.75+1.5*.9+1*.8)/10 = 0.7225 → 3.9.
        self.assertEqual(result.value, Decimal("3.9"))
        self.assertEqual(result.label, "Healthy")
        self.assertEqual(result.category, 1)
        self.assertEqual(
            [(r.key, str(r.reading)) for r in result.readings],
            [
                ("ai_pulse", "4.0"),
                ("csm_pulse", "3.0"),
                ("sentiment", "4.0"),
                ("touch", "4.6"),
                ("support", "4.2"),
            ],
        )

    def test_missing_signals_are_excluded_not_scored_zero(self):
        only_support = compute()
        # Nothing but "0 open tickets" → 5.0 from that one signal, not dragged
        # down by four signals with nothing to measure.
        self.assertEqual(only_support.value, Decimal("5.0"))
        self.assertEqual(
            [r.available for r in only_support.readings], [False, False, False, False, True]
        )
        self.assertEqual(only_support.label, "Thriving")

    def test_the_csm_pulse_ages_half_weight_after_a_month_and_out_after_three(self):
        fresh = compute(ai_pulse_value=5, csm_pulse_score=1, csm_pulse_age_days=10)
        half = compute(ai_pulse_value=5, csm_pulse_score=1, csm_pulse_age_days=60)
        gone = compute(ai_pulse_value=5, csm_pulse_score=1, csm_pulse_age_days=120)
        csm = lambda p: next(r for r in p.readings if r.key == "csm_pulse")  # noqa: E731
        self.assertEqual(csm(fresh).weight, Decimal("2.5"))
        self.assertEqual(csm(half).weight, Decimal("1.25"))
        self.assertIn("half weight", csm(half).note)
        self.assertFalse(csm(gone).available)
        self.assertIn("too old", csm(gone).note)
        self.assertLess(fresh.value, half.value)
        self.assertLess(half.value, gone.value)

    def test_labels_and_history_categories(self):
        cases = [
            (compute(ai_pulse_value=1, open_ticket_count=10), "Critical", 2),
            (compute(ai_pulse_value=2, open_ticket_count=10), "At risk", 2),
            (compute(ai_pulse_value=3, open_ticket_count=5), "Watch", 3),
            (compute(ai_pulse_value=4, open_ticket_count=1), "Healthy", 1),
            (compute(ai_pulse_value=5, open_ticket_count=0), "Thriving", 1),
        ]
        for result, label, category in cases:
            with self.subTest(label=label):
                self.assertEqual((result.label, result.category), (label, category))


class AccountPulseEndToEndTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.account = Account.objects.create(name="North America", ai_pulse_value=4)
        self.account.customers.add(self.customer)
        now = timezone.now()
        Email.objects.create(
            account=self.account,
            subject="Thanks",
            body="All good",
            sent_at=now - timedelta(days=3),
            sentiment="positive",
        )
        Call.objects.create(
            account=self.account,
            title="Check-in",
            occurred_at=now - timedelta(days=40),
            sentiment="negative",
        )
        Ticket.objects.create(
            account=self.account,
            title="SSO broken",
            opened_at=(now - timedelta(days=2)).date(),
            sentiment="negative",
        )

    def test_the_api_exposes_the_computed_pulse_with_its_breakdown(self):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/"
        data = self.client.get(url).data["account_pulse"]
        by = {r["key"]: r for r in data["breakdown"]}
        self.assertEqual(by["ai_pulse"]["reading"], "4.0")
        self.assertIsNone(by["csm_pulse"]["reading"])
        # Within 30 days: the positive email and the negative ticket; the
        # 40-day-old negative call is outside the window.
        self.assertEqual(by["sentiment"]["note"], "1 positive, 1 negative of 2 in the last 30 days")
        self.assertEqual(by["sentiment"]["reading"], "3.0")
        self.assertEqual(by["touch"]["note"], "3 days ago")
        self.assertEqual(by["support"]["note"], "1 open")
        # (3*0.75 + 2*0.5 + 1.5*(1-3/90) + 1*0.9) / 7.5 = 0.7533 → 4.0
        self.assertEqual(data["value"], "4.0")
        self.assertEqual(data["label"], "Healthy")
        # The list endpoint says the same (annotated path).
        listed = self.client.get("/api/v1/accounts/").data
        rows = listed["results"] if isinstance(listed, dict) else listed
        self.assertEqual(rows[0]["account_pulse"]["value"], "4.0")

    def test_the_daily_job_appends_one_history_dot_per_day(self):
        call_command("run_health_maintenance", verbosity=0)
        self.account.refresh_from_db()
        self.assertEqual(self.account.pulse, [1])
        self.assertEqual(self.account.pulse_recorded_on, timezone.localdate())
        call_command("run_health_maintenance", verbosity=0)
        self.account.refresh_from_db()
        self.assertEqual(self.account.pulse, [1])  # once a day


class OrganisationPulseTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.customer = Customer.objects.create(
            organisation=self.org, name="Globex", ai_pulse_value=2
        )
        self.account = Account.objects.create(name="North America")
        self.account.customers.add(self.customer)
        now = timezone.now()
        # One conversation on the organisation itself, two on its account.
        Email.objects.create(
            customer=self.customer,
            subject="Escalation",
            body="Not happy",
            sent_at=now - timedelta(days=1),
            sentiment="negative",
        )
        Email.objects.create(
            account=self.account,
            subject="Thanks",
            body="Great",
            sent_at=now - timedelta(days=2),
            sentiment="positive",
        )
        Ticket.objects.create(
            account=self.account,
            title="Login fails",
            opened_at=(now - timedelta(days=5)).date(),
            sentiment="positive",
        )

    def test_the_organisations_pulse_counts_its_accounts_conversations_as_its_own(self):
        by = {r.key: r for r in self.customer.account_pulse().readings}
        self.assertEqual(by["sentiment"].note, "2 positive, 1 negative of 3 in the last 30 days")
        self.assertEqual(by["touch"].note, "1 days ago")
        self.assertEqual(by["support"].note, "1 open")
        self.assertEqual(by["ai_pulse"].reading, Decimal("2.0"))

    def test_the_customer_endpoints_expose_it_through_the_annotated_path(self):
        self.client.force_authenticate(self.admin)
        detail = self.client.get(f"/api/v1/customers/{self.customer.id}/").data["account_pulse"]
        listed = self.client.get("/api/v1/customers/").data
        rows = listed["results"] if isinstance(listed, dict) else listed
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(
            detail["breakdown"][2]["note"], "2 positive, 1 negative of 3 in the last 30 days"
        )
        self.assertEqual(by_id[self.customer.id]["account_pulse"], detail)

    def test_the_daily_job_records_a_dot_for_the_organisation_and_its_account(self):
        call_command("run_health_maintenance", verbosity=0)
        self.customer.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(len(self.customer.pulse), 1)
        self.assertEqual(len(self.account.pulse), 1)
        self.assertEqual(self.customer.pulse_recorded_on, timezone.localdate())
