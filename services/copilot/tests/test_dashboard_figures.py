"""Each area's figures equal what that area's endpoint returns for the same
viewer and filters — the screen and the assistant can never disagree."""

from datetime import timedelta
from decimal import Decimal

from services.copilot import dashboard_figures
from services.copilot.dashboard_context import clean_filters
from services.customers import forecast
from services.customers.models import HealthSnapshot, Opportunity

from .dashboard_fixture import AVERAGE, GOOD, POOR, DashboardFixture


class RevenueFiguresTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.shaky = self.customer(
            "Shaky",
            health_score=POOR,
            renewal_date=self.today + timedelta(days=30),
            arr=50_000,
            lifecycle_stage="renewal",
        )
        self.growing = self.customer("Growing", arr=80_000, lifecycle_stage="live")
        Opportunity.objects.create(
            customer=self.growing,
            title="Seats",
            mrr=Decimal("1000"),
            stage=Opportunity.Stage.NEGOTIATION,
        )
        self.theirs = self.customer(
            "Theirs",
            owner=self.other,
            health_score=POOR,
            renewal_date=self.today + timedelta(days=30),
            arr=900_000,
        )

    def test_equals_the_forecast_endpoint(self):
        for params in (
            {},
            {"owner": str(self.csm.pk)},
            {"lifecycle": "renewal"},
            {"customer": str(self.growing.pk)},
        ):
            with self.subTest(params=params):
                figures = dashboard_figures.revenue_figures(self.csm, clean_filters(params))
                screen = self.api.get("/api/v1/customers/forecast/", params).data

                self.assertEqual(figures["bridge"], screen["bridge"])
                self.assertEqual(figures["currency"], screen["currency"])
                self.assertEqual(
                    figures["at_risk"],
                    round(screen["bridge"]["churn"] + screen["bridge"]["contraction"], 2),
                )
                self.assertEqual(
                    [(m["id"], m["net"]) for m in figures["movers"]],
                    [(row["id"], row["net"]) for row in screen["swing"][:10]],
                )

    def test_another_csms_customer_is_never_counted(self):
        figures = dashboard_figures.revenue_figures(self.csm, clean_filters({}))

        self.assertEqual(figures["bridge"]["opening_arr"], 130000.0)
        self.assertNotIn(self.theirs.pk, [m["id"] for m in figures["movers"]])

    def test_unpriced_count_equals_the_forecast_endpoint(self):
        # No FxRate is configured for JPY on this organisation, so this
        # account's ARR cannot be converted and it counts as unpriced.
        self.customer("Tokyo", arr=100_000, currency="JPY")

        figures = dashboard_figures.revenue_figures(self.csm, clean_filters({}))
        screen = self.api.get("/api/v1/customers/forecast/", {}).data

        self.assertEqual(figures["unpriced_count"], screen["unpriced_count"])
        self.assertGreater(figures["unpriced_count"], 0)


class HealthFiguresTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.poor = self.customer(
            "Poor soon", health_score=POOR, renewal_date=self.today + timedelta(days=20)
        )
        self.average = self.customer("Average", health_score=AVERAGE, lifecycle_stage="live")
        self.good = self.customer("Good", health_score=GOOD, lifecycle_stage="live")
        for months_ago, score in ((2, GOOD), (1, AVERAGE)):
            HealthSnapshot.objects.create(
                customer=self.average,
                captured_on=self.today - timedelta(days=31 * months_ago),
                health_score=score,
            )
        self.customer("Theirs", owner=self.other, health_score=POOR)

    def test_equals_the_health_endpoint(self):
        rows = self.api.get("/api/v1/customers/health/").data["results"]
        for params in ({}, {"lifecycle": "live"}, {"customer": str(self.poor.pk)}):
            with self.subTest(params=params):
                filters = clean_filters(params)
                ids = set(
                    forecast.filtered_customers(self.csm, filters).values_list("pk", flat=True)
                )
                # What the screen does: the whole book, filtered client-side.
                shown = [row for row in rows if row["id"] in ids]
                acting = sorted(
                    (row for row in shown if row["triage_score"] >= 40),
                    key=lambda row: (-row["triage_score"], row["name"]),
                )

                figures = dashboard_figures.health_figures(self.csm, filters, today=self.today)

                self.assertEqual(figures["total"], len(shown))
                self.assertEqual(
                    figures["at_good"], sum(1 for row in shown if row["health_category"] == "good")
                )
                self.assertEqual(figures["needs_action"], len(acting))
                for direction in dashboard_figures.DIRECTIONS:
                    self.assertEqual(
                        figures["by_direction"][direction],
                        sum(1 for row in shown if row["triage_direction"] == direction),
                    )
                self.assertEqual(
                    [(a["id"], a["score"]) for a in figures["accounts_needing_action"]],
                    [(row["id"], row["triage_score"]) for row in acting[:10]],
                )
                self.assertEqual(
                    [a["factors"] for a in figures["accounts_needing_action"]],
                    [[f["label"] for f in row["triage_factors"]] for row in acting[:10]],
                )

    def test_summary_counts(self):
        figures = dashboard_figures.health_figures(self.csm, clean_filters({}), today=self.today)

        self.assertEqual(figures["total"], 3)
        self.assertEqual(figures["at_good"], 1)
        self.assertEqual(figures["needs_action"], 1)
        self.assertEqual(figures["needs_action_renewing_soon"], 1)
        self.assertEqual(figures["by_direction"]["declining"], 1)
        self.assertEqual(figures["accounts_needing_action"][0]["name"], "Poor soon")

    def test_blind_spots_matches_the_frontend_rule(self):
        # The frontend's `summarise()` counts a blind spot where
        # `pulseGap >= 2` (csmPulseScore - aiPulseScore, both rated). A gap of
        # exactly one point short of that must not count.
        self.customer("Blind spot", csm_pulse_score=5, ai_pulse_value=3)
        self.customer("Just misses", csm_pulse_score=5, ai_pulse_value=4)

        figures = dashboard_figures.health_figures(self.csm, clean_filters({}), today=self.today)

        self.assertEqual(figures["blind_spots"], 1)

    def test_ignores_snapshots_outside_the_health_view_window(self):
        # Both snapshots sit outside the Health view's default 12-month
        # window (31 * 12 = 372 days), so they must not reach the trail —
        # unwindowed they would read as a GOOD -> POOR decline.
        old_trail = self.customer("Old trail", health_score=POOR, lifecycle_stage="live")
        HealthSnapshot.objects.create(
            customer=old_trail,
            captured_on=self.today - timedelta(days=400),
            health_score=GOOD,
        )
        HealthSnapshot.objects.create(
            customer=old_trail,
            captured_on=self.today - timedelta(days=380),
            health_score=POOR,
        )

        filters = clean_filters({})
        rows = self.api.get("/api/v1/customers/health/").data["results"]
        ids = set(forecast.filtered_customers(self.csm, filters).values_list("pk", flat=True))
        shown = [row for row in rows if row["id"] in ids]

        screen_row = next(row for row in shown if row["id"] == old_trail.pk)
        self.assertEqual(screen_row["triage_direction"], "unknown")

        figures = dashboard_figures.health_figures(self.csm, filters, today=self.today)

        for direction in dashboard_figures.DIRECTIONS:
            self.assertEqual(
                figures["by_direction"][direction],
                sum(1 for row in shown if row["triage_direction"] == direction),
            )
