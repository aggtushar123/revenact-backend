"""The Revenue Forecast: the churn rule, and the ARR bridge built on it.

Every number here ends up in a board pack, so the tests are about the rules
rather than about shapes: what the weighting does, what stops it double
counting, and what stops it producing a figure nobody could act on (a negative
worst case, a forecast that disagrees with the Renewal tab).
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers import churn
from services.customers.models import Activity, Customer, Opportunity, Risk


class ChurnRuleTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def _customer(self, **overrides):
        # Live by default: `lifecycle_stage` defaults to onboarding on the
        # model, which is itself one of the risk factors — a fixture that took
        # the default would be testing two rules at once.
        overrides.setdefault("lifecycle_stage", Customer.LifecycleStage.LIVE)
        return Customer.objects.create(
            organisation=self.org, name=overrides.pop("name", "Acme"), **overrides
        )

    def test_the_health_grade_sets_the_base_rate(self):
        good = self._customer(name="Good", health_score=Decimal("9.0"))
        poor = self._customer(name="Poor", health_score=Decimal("2.0"))

        self.assertEqual(churn.risk_of_loss(good)[0], churn.BASE_RISK["good"])
        self.assertEqual(churn.risk_of_loss(poor)[0], churn.BASE_RISK["poor"])

    def test_a_cold_account_carries_more_risk_and_says_why(self):
        customer = self._customer(health_score=Decimal("9.0"))

        risk, factors = churn.risk_of_loss(customer, days_since_touch=90)

        self.assertAlmostEqual(
            risk, churn.BASE_RISK["good"] + churn.RISK_ADJUSTMENTS["cold_contact"]
        )
        self.assertIn("No contact in 90 days", [factor["label"] for factor in factors])

    def test_an_unknown_contact_age_is_not_treated_as_stale(self):
        # None means nobody looked, which is not the same as nobody called.
        customer = self._customer(health_score=Decimal("9.0"))

        self.assertEqual(
            churn.risk_of_loss(customer, days_since_touch=None)[0], churn.BASE_RISK["good"]
        )

    def test_disagreeing_pulses_add_risk_but_one_point_of_difference_does_not(self):
        disagree = self._customer(
            name="Split", health_score=Decimal("9.0"), csm_pulse_score=5, ai_pulse_value=2
        )
        rounding = self._customer(
            name="Close", health_score=Decimal("9.0"), csm_pulse_score=4, ai_pulse_value=3
        )

        self.assertAlmostEqual(
            churn.risk_of_loss(disagree)[0],
            churn.BASE_RISK["good"] + churn.RISK_ADJUSTMENTS["pulse_disagreement"],
        )
        self.assertEqual(churn.risk_of_loss(rounding)[0], churn.BASE_RISK["good"])

    def test_an_unrated_pulse_is_neither_agreement_nor_disagreement(self):
        customer = self._customer(
            health_score=Decimal("9.0"), csm_pulse_score=None, ai_pulse_value=1
        )

        self.assertEqual(churn.risk_of_loss(customer)[0], churn.BASE_RISK["good"])

    def test_an_account_that_has_not_landed_yet_carries_more_risk(self):
        customer = self._customer(
            health_score=Decimal("9.0"), lifecycle_stage=Customer.LifecycleStage.KICKOFF
        )

        risk, factors = churn.risk_of_loss(customer)

        self.assertAlmostEqual(
            risk, churn.BASE_RISK["good"] + churn.RISK_ADJUSTMENTS["not_embedded"]
        )
        self.assertIn("Still in kickoff", [factor["label"] for factor in factors])

    def test_a_live_account_gets_no_lifecycle_adjustment(self):
        customer = self._customer(
            health_score=Decimal("9.0"), lifecycle_stage=Customer.LifecycleStage.LIVE
        )

        self.assertEqual(churn.risk_of_loss(customer)[0], churn.BASE_RISK["good"])

    def test_risk_never_reaches_certainty(self):
        # A 100% line item invites people to stop working the deal.
        customer = self._customer(
            health_score=Decimal("1.0"),
            csm_pulse_score=5,
            ai_pulse_value=1,
            lifecycle_stage=Customer.LifecycleStage.ONBOARDING,
        )

        self.assertLessEqual(churn.risk_of_loss(customer, days_since_touch=400)[0], churn.MAX_RISK)


class ForecastViewTests(APITestCase):
    url = "/api/v1/customers/forecast/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.today = timezone.localdate()
        self.client.force_authenticate(self.csm)

    def _customer(self, name, arr, *, renews_in_days=90, health="9.0", **overrides):
        return Customer.objects.create(
            organisation=self.org,
            name=name,
            owner=overrides.pop("owner", self.csm),
            arr_billed_at_account=Decimal(arr),
            renewal_date=(
                None if renews_in_days is None else self.today + timedelta(days=renews_in_days)
            ),
            health_score=Decimal(health),
            # Live unless the caller says otherwise: the model defaults to
            # onboarding, which is itself a risk factor, and a fixture that
            # took that default would be testing two rules at once.
            lifecycle_stage=overrides.pop("lifecycle_stage", Customer.LifecycleStage.LIVE),
            **overrides,
        )

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_another_owners_book_is_invisible(self):
        self._customer("Mine", 100_000)
        self._customer("Theirs", 900_000, owner=self.other)

        self.assertEqual(self.client.get(self.url).data["bridge"]["opening_arr"], 100_000.0)

    def test_a_churned_customer_is_not_in_the_opening_balance(self):
        """The bug this filter exists for: a churned-but-unarchived customer
        was contributing live ARR to the forecast's opening balance."""
        self._customer("Live", 100_000)
        self._customer("Left", 500_000, churn_date=self.today - timedelta(days=30))

        self.assertEqual(self.client.get(self.url).data["bridge"]["opening_arr"], 100_000.0)

    # ── the bridge ───────────────────────────────────────────────────

    def test_the_bridge_weights_churn_by_the_same_rule_the_renewal_tab_prints(self):
        self._customer("Shaky", 100_000, health="2.0")  # Poor → 0.5

        bridge = self.client.get(self.url).data["bridge"]

        self.assertEqual(bridge["opening_arr"], 100_000.0)
        self.assertEqual(bridge["churn"], 50_000.0)
        self.assertEqual(bridge["forecast_arr"], 50_000.0)

    def test_a_renewal_beyond_the_horizon_cannot_churn_inside_it(self):
        # However bad the account looks, it cannot be lost at a renewal that
        # does not happen this year.
        self._customer("Far Off", 100_000, renews_in_days=800, health="2.0")

        bridge = self.client.get(self.url).data["bridge"]

        self.assertEqual(bridge["churn"], 0.0)
        self.assertEqual(bridge["forecast_arr"], 100_000.0)

    def test_an_overdue_renewal_is_inside_the_horizon(self):
        self._customer("Overdue", 100_000, renews_in_days=-30, health="2.0")

        self.assertEqual(self.client.get(self.url).data["bridge"]["churn"], 50_000.0)

    def test_open_opportunities_are_weighted_by_stage(self):
        customer = self._customer("Growing", 100_000, renews_in_days=800)
        Opportunity.objects.create(
            customer=customer,
            title="Seats",
            mrr=Decimal("1000"),  # 12k/yr
            stage=Opportunity.Stage.NEGOTIATION,  # 0.8
        )

        bridge = self.client.get(self.url).data["bridge"]

        self.assertEqual(bridge["expansion"], 9_600.0)
        self.assertEqual(bridge["forecast_arr"], 109_600.0)

    def test_open_risks_are_weighted_by_priority(self):
        customer = self._customer("Wobbly", 100_000, renews_in_days=800)
        Risk.objects.create(
            customer=customer,
            title="Sponsor left",
            mrr=Decimal("1000"),  # 12k/yr
            priority=Risk.Priority.HIGH,  # 0.6
            stage=Risk.Stage.OPEN,
        )

        bridge = self.client.get(self.url).data["bridge"]

        self.assertEqual(bridge["contraction"], 7_200.0)

    def test_a_closed_risk_is_not_a_forecast_question(self):
        customer = self._customer("Handled", 100_000, renews_in_days=800)
        Risk.objects.create(
            customer=customer,
            title="Mitigated",
            mrr=Decimal("5000"),
            priority=Risk.Priority.HIGH,
            stage=Risk.Stage.MITIGATED,
        )

        self.assertEqual(self.client.get(self.url).data["bridge"]["contraction"], 0.0)

    def test_the_same_arr_is_never_lost_twice(self):
        """An account both renewing badly and carrying a risk contributes the
        larger of the two, not the sum."""
        customer = self._customer("Double", 100_000, health="2.0")  # churn 50k
        Risk.objects.create(
            customer=customer,
            title="Downgrade",
            mrr=Decimal("1000"),  # 12k/yr × 0.6 = 7.2k
            priority=Risk.Priority.HIGH,
            stage=Risk.Stage.OPEN,
        )

        bridge = self.client.get(self.url).data["bridge"]

        self.assertEqual(bridge["churn"] + bridge["contraction"], 50_000.0)

    def test_the_downside_cannot_exceed_what_the_account_pays(self):
        # A risk larger than the contract it hangs off used to make the worst
        # case negative — a forecast saying the book will owe money.
        customer = self._customer("Small", 10_000, renews_in_days=800)
        Risk.objects.create(
            customer=customer,
            title="Enormous",
            mrr=Decimal("50000"),
            priority=Risk.Priority.HIGH,
            stage=Risk.Stage.OPEN,
        )

        data = self.client.get(self.url).data

        self.assertEqual(data["bridge"]["contraction"], 10_000.0)
        self.assertEqual(data["bridge"]["forecast_arr"], 0.0)
        self.assertGreaterEqual(data["scenarios"]["worst"], 0.0)

    def test_nrr_is_null_rather_than_a_fake_hundred_on_an_empty_book(self):
        self.assertIsNone(self.client.get(self.url).data["bridge"]["nrr"])

    # ── scenarios ────────────────────────────────────────────────────

    def test_the_worst_case_only_loses_what_can_be_lost_this_year(self):
        # A floor nobody believes is a floor nobody uses: an account renewing
        # next year cannot be lost in this window.
        self._customer("This Year", 100_000, renews_in_days=30)
        self._customer("Next Year", 400_000, renews_in_days=700)

        scenarios = self.client.get(self.url).data["scenarios"]

        self.assertEqual(scenarios["worst"], 400_000.0)

    def test_the_best_case_closes_the_whole_pipeline_and_loses_nothing(self):
        customer = self._customer("Upside", 100_000, health="2.0")
        Opportunity.objects.create(
            customer=customer,
            title="Big",
            mrr=Decimal("1000"),
            stage=Opportunity.Stage.DISCOVERY,  # only 0.1 weighted
        )

        scenarios = self.client.get(self.url).data["scenarios"]

        self.assertEqual(scenarios["best"], 112_000.0)
        self.assertLess(scenarios["likely"], scenarios["best"])
        self.assertLess(scenarios["worst"], scenarios["likely"])

    # ── the swing list and the pipeline ──────────────────────────────

    def test_the_swing_list_ranks_by_how_far_an_account_moves_the_number(self):
        """Both directions in one list: a forecast review works one list of
        names, and an account that is both is the one to talk about."""
        big_down = self._customer("Falling", 200_000, health="2.0")  # −100k
        up = self._customer("Rising", 100_000, renews_in_days=800)
        Opportunity.objects.create(
            customer=up, title="Expand", mrr=Decimal("2000"), stage=Opportunity.Stage.CLOSED_WON
        )

        swing = self.client.get(self.url).data["swing"]

        self.assertEqual([row["name"] for row in swing], ["Falling", "Rising"])
        self.assertEqual(swing[0]["id"], big_down.id)
        self.assertLess(swing[0]["net"], 0)
        self.assertGreater(swing[1]["net"], 0)

    def test_each_swing_row_carries_the_reasons_behind_its_risk(self):
        customer = self._customer("Explained", 100_000, health="2.0")
        Activity.objects.create(
            customer=customer,
            type=Activity.ActivityType.HEALTH_CHECK_REVIEW,
            occurred_at=self.today - timedelta(days=120),
        )

        row = self.client.get(self.url).data["swing"][0]

        labels = [factor["label"] for factor in row["factors"]]
        self.assertIn("Poor health", labels)
        self.assertIn("No contact in 120 days", labels)

    def test_pipeline_reports_weighted_and_open_side_by_side(self):
        # The gap between them is how much of the upside is a conversation
        # rather than a commitment.
        customer = self._customer("Pipeline", 100_000, renews_in_days=800)
        Opportunity.objects.create(
            customer=customer, title="Early", mrr=Decimal("1000"), stage=Opportunity.Stage.DISCOVERY
        )

        stage = next(
            row for row in self.client.get(self.url).data["pipeline"] if row["key"] == "discovery"
        )

        self.assertEqual(stage["open"], 12_000.0)
        self.assertEqual(stage["weighted"], 1_200.0)
        self.assertEqual(stage["count"], 1)

    # ── window and filters ───────────────────────────────────────────

    def test_the_horizon_can_be_moved_and_is_clamped(self):
        self._customer("Later", 100_000, renews_in_days=500, health="2.0")

        self.assertEqual(self.client.get(self.url).data["bridge"]["churn"], 0.0)
        self.assertEqual(
            self.client.get(self.url, {"horizon_days": 600}).data["bridge"]["churn"], 50_000.0
        )
        # Clamped rather than rejected: a dashboard should draw.
        self.assertEqual(
            self.client.get(self.url, {"horizon_days": 99999}).data["horizon_days"], 1095
        )
        self.assertEqual(
            self.client.get(self.url, {"horizon_days": "abc"}).data["horizon_days"], 365
        )

    def test_filters_narrow_the_book(self):
        mine = self._customer("Mine", 100_000)
        self._customer("Other", 400_000, lifecycle_stage=Customer.LifecycleStage.CHURN)

        self.assertEqual(
            self.client.get(self.url, {"customer": mine.id}).data["bridge"]["opening_arr"],
            100_000.0,
        )
        self.assertEqual(
            self.client.get(self.url, {"lifecycle": "churn"}).data["bridge"]["opening_arr"],
            400_000.0,
        )

    def test_an_unconvertible_arr_is_counted_and_left_out_of_the_money(self):
        self._customer("Local", 100_000)
        self._customer("Tokyo", 100_000, currency="JPY")

        data = self.client.get(self.url).data

        self.assertEqual(data["unpriced_count"], 1)
        self.assertEqual(data["bridge"]["opening_arr"], 100_000.0)

    # ── drill ────────────────────────────────────────────────────────

    def test_drill_values_sum_to_the_bridge_step(self):
        self._customer("Risky", 100_000, renews_in_days=30, health="2.0")
        self._customer("Safe", 50_000, renews_in_days=400)
        stats = self.client.get(self.url).json()
        bridge = stats["bridge"]
        for segment, step in [("churn", "churn"), ("contraction", "contraction")]:
            with self.subTest(segment=segment):
                drill = self.client.get(self.url, {"drill": segment}).json()["drill"]
                self.assertAlmostEqual(
                    sum(c["value"] for c in drill["companies"]), bridge[step], places=2
                )
        at_risk = self.client.get(self.url, {"drill": "at_risk"}).json()["drill"]
        self.assertAlmostEqual(
            sum(c["value"] for c in at_risk["companies"]),
            bridge["churn"] + bridge["contraction"],
            places=2,
        )
        self.assertEqual(at_risk["value_label"], "downside")

    def test_drill_is_the_whole_list_not_the_top_fifteen(self):
        for i in range(20):
            self._customer(f"Risky {i}", 10_000 + i, renews_in_days=30, health="2.0")
        drill = self.client.get(self.url, {"drill": "at_risk"}).json()["drill"]
        self.assertEqual(drill["count"], 20)

    def test_drill_respects_the_horizon_and_filters(self):
        self._customer("Later", 100_000, renews_in_days=200, health="2.0")
        short = self.client.get(self.url, {"drill": "churn", "horizon_days": 90}).json()["drill"]
        long = self.client.get(self.url, {"drill": "churn", "horizon_days": 365}).json()["drill"]
        self.assertEqual(short["companies"], [])
        self.assertEqual([c["name"] for c in long["companies"]], ["Later"])
