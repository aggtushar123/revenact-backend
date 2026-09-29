from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from services.accounts_portfolio import book, shape
from services.accounts_portfolio.params import parse_params
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture


class NpsBandTests(SimpleTestCase):
    def test_by_sign_like_the_filter(self):
        self.assertEqual(shape.nps_band(50), "promoter")
        self.assertEqual(shape.nps_band(0), "passive")
        self.assertEqual(shape.nps_band(-1), "detractor")
        self.assertIsNone(shape.nps_band(None))


class SummaryTests(AccountPortfolioFixture):
    """Four accounts. The tiles must reproduce `/accounts/stats/` (today's
    MetricsPanel) and list exactly their N when clicked."""

    def setUp(self):
        super().setUp()
        self.account(
            "Good",
            lifecycle_stage="live",
            nps_score=50,
            renewal_date=self.today + timedelta(days=20),
        )
        self.account(
            "Average",
            health_score=Decimal("5.0"),
            lifecycle_stage="adoption",
            nps_score=0,
            arr=Decimal("24000"),
            renewal_date=self.today + timedelta(days=60),
        )
        self.account(
            "Poor",
            health_score=Decimal("2.0"),
            lifecycle_stage="renewal",
            nps_score=-40,
            arr=Decimal("6000"),
            renewal_date=self.today - timedelta(days=3),
        )
        self.account(
            "Unscored",
            owner=None,
            lifecycle_stage="churn",
            arr=Decimal("1000"),
            renewal_date=self.today + timedelta(days=120),
        )

    def portfolio(self, user, **query):
        return book.load_portfolio(user, parse_params(query), today=self.today)

    def summary(self, user, **query):
        return shape.build_summary(self.portfolio(user, **query).entries)

    def test_the_numbers(self):
        summary = self.summary(self.admin)
        self.assertEqual(summary["accounts"], 4)
        self.assertEqual(summary["arr"], 43000.0)
        self.assertEqual(summary["unconverted_count"], 0)
        self.assertEqual(summary["renewing"], {"30": 2, "90": 3})
        self.assertEqual(
            summary["nps"], {"promoters": 1, "passives": 1, "detractors": 1, "score": 0}
        )
        self.assertEqual(
            (summary["health"]["good"], summary["health"]["average"], summary["health"]["poor"]),
            (2, 1, 1),
        )
        self.assertEqual(summary["health"]["arr"]["good"], 13000.0)
        self.assertEqual(summary["health"]["mrr"]["average"], 2000.0)
        stages = {row["value"]: row for row in summary["lifecycle"]}
        self.assertEqual(stages["churn"]["count"], 1)
        self.assertEqual(
            stages["kickoff"], {"value": "kickoff", "label": "Kickoff", "count": 0, "arr": 0.0}
        )

    def test_the_tiles_reproduce_account_stats(self):
        for user in (self.admin, self.csm):
            with self.subTest(user=user.name):
                api = APIClient()
                api.force_authenticate(user)
                stats = api.get("/api/v1/accounts/stats/").data
                summary = self.summary(user)
                for category in ("good", "average", "poor"):
                    self.assertEqual(
                        summary["health"][category], stats["health"][category]["count"]
                    )
                    self.assertEqual(
                        summary["health"]["arr"][category], stats["health"][category]["arr"]
                    )
                    self.assertEqual(
                        summary["health"]["mrr"][category], stats["health"][category]["mrr"]
                    )
                self.assertEqual(summary["nps"], stats["nps"])
                self.assertEqual(
                    {
                        row["value"]: {"count": row["count"], "arr": row["arr"]}
                        for row in summary["lifecycle"]
                    },
                    {
                        stage: {"count": bucket["count"], "arr": bucket["arr"]}
                        for stage, bucket in stats["lifecycle"].items()
                    },
                )

    def test_a_clicked_tile_lists_exactly_its_number(self):
        summary = self.summary(self.admin)

        def count(**query):
            return len(self.portfolio(self.admin, **query).entries)

        for days in ("30", "90"):
            self.assertEqual(count(renews_within=days), summary["renewing"][days])
        for band, key in (
            ("promoter", "promoters"),
            ("passive", "passives"),
            ("detractor", "detractors"),
        ):
            self.assertEqual(count(nps=band), summary["nps"][key])
        for category in ("good", "average", "poor"):
            self.assertEqual(count(health=category), summary["health"][category])
        for row in summary["lifecycle"]:
            self.assertEqual(count(lifecycle=row["value"]), row["count"])

    def test_the_listing_body(self):
        params = parse_params({"group": "health", "group_value": "good", "limit": "1"})
        body = shape.build_listing(self.portfolio(self.admin), params, filters={"owners": []})
        self.assertEqual(
            set(body),
            {"results", "next_cursor", "count", "groups", "summary", "filters", "currency"},
        )
        self.assertEqual(body["count"], 2)
        self.assertEqual(len(body["results"]), 1)
        self.assertIsNotNone(body["next_cursor"])
        self.assertEqual(sum(g["count"] for g in body["groups"]), 4)
        self.assertEqual(body["summary"]["accounts"], 4)
        self.assertEqual(body["filters"], {"owners": []})
        self.assertEqual(body["currency"], "USD")
