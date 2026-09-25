from datetime import timedelta
from decimal import Decimal

from rest_framework.test import APIClient

from services.accounts.models import User
from services.customers.models import Customer, Product
from services.fx_rates.models import FxRate
from services.organizations import book, shape
from services.organizations.params import parse_params
from services.organizations.tests.fixtures import PortfolioFixture


class SummaryEqualsTodaysPanelTests(PortfolioFixture):
    """The tiles reproduce `/customers/stats/` and the list's `renewal_within`
    count on the same book. Churned rows carry both markers here, the way the
    churn modal writes them (see the plan's pre-flight row 2)."""

    def setUp(self):
        super().setUp()
        FxRate.objects.create(
            organisation=self.org, currency="EUR", rate_to_org_currency=Decimal("1.1")
        )
        self.customer(
            "Good",
            health_score=Decimal("8.0"),
            lifecycle_stage="live",
            nps_score=50,
            renewal_date=self.today + timedelta(days=20),
        )
        self.customer(
            "Average",
            health_score=Decimal("5.0"),
            lifecycle_stage="adoption",
            nps_score=0,
            arr_billed_at_account=Decimal("24000"),
            renewal_date=self.today + timedelta(days=60),
        )
        self.customer(
            "Poor euro",
            health_score=Decimal("2.0"),
            lifecycle_stage="live",
            nps_score=-10,
            currency="EUR",
            arr_billed_at_account=Decimal("10000"),
            renewal_date=self.today - timedelta(days=5),
        )
        self.customer(
            "Poor pound",
            health_score=Decimal("3.0"),
            lifecycle_stage="renewal",
            currency="GBP",
            renewal_date=self.today + timedelta(days=200),
        )
        self.customer("Archived", is_archived=True, arr_billed_at_account=Decimal("99999"))
        self.customer(
            "Churned",
            lifecycle_stage="churn",
            churn_date=self.today - timedelta(days=30),
            renewal_date=self.today + timedelta(days=5),
        )
        self.api = APIClient()
        self.api.force_authenticate(self.csm)

    def summary(self, **query):
        portfolio = book.load_portfolio(self.csm, parse_params(query), today=self.today)
        return shape.build_summary(portfolio.entries)

    def test_health_nps_and_lifecycle_equal_customer_stats(self):
        stats = self.api.get("/api/v1/customers/stats/").data
        summary = self.summary()
        for category in ("good", "average", "poor"):
            self.assertEqual(summary["health"][category], stats["health"][category]["count"])
            self.assertEqual(summary["health"]["arr"][category], stats["health"][category]["arr"])
            self.assertEqual(summary["health"]["mrr"][category], stats["health"][category]["mrr"])
        self.assertEqual(summary["nps"], stats["nps"])
        self.assertEqual(
            {row["value"]: row["count"] for row in summary["lifecycle"]},
            {stage: bucket["count"] for stage, bucket in stats["lifecycle"].items()},
        )
        self.assertEqual(summary["unconverted_count"], stats["unconverted_count"])

    def test_accounts_and_arr(self):
        summary = self.summary()
        self.assertEqual(summary["accounts"], 4)
        self.assertEqual(summary["arr"], 12000.0 + 24000.0 + 11000.0)
        self.assertEqual(summary["unconverted_count"], 1)
        live = next(row for row in summary["lifecycle"] if row["value"] == "live")
        self.assertEqual(live, {"value": "live", "label": "Live", "count": 2, "arr": 23000.0})

    def test_renewing_equals_the_lists_renewal_within(self):
        summary = self.summary()
        for days in (30, 90):
            listed = self.api.get("/api/v1/customers/", {"renewal_within": days}).data["count"]
            self.assertEqual(summary["renewing"][str(days)], listed)
        self.assertEqual(summary["renewing"], {"30": 2, "90": 3})

    def test_totals_follow_the_filters(self):
        summary = self.summary(health="poor")
        self.assertEqual(summary["accounts"], 2)
        self.assertEqual(summary["health"]["good"], 0)
        # A churned row counted when asked for, never towards renewals.
        with_churned = self.summary(include_churned="1")
        self.assertEqual(with_churned["accounts"], 5)
        self.assertEqual(with_churned["renewing"]["30"], 2)

    def test_clicking_the_renewing_tile_lists_the_same_n(self):
        """The tile and `renews_within` read one churned rule — a churn date or
        the Churn stage — even when churned rows are shown: a Live account
        with a churn date has left, and so has the Churn-stage one."""
        self.customer(
            "Left but live",
            lifecycle_stage="live",
            churn_date=self.today - timedelta(days=2),
            renewal_date=self.today + timedelta(days=7),
        )
        self.customer(
            "Churn stage, no date",
            lifecycle_stage="churn",
            renewal_date=self.today + timedelta(days=7),
        )
        for query in ({}, {"include_churned": "1"}, {"include_churned": "1", "health": "good"}):
            for days in (30, 90):
                with self.subTest(query=query, days=days):
                    tile = self.summary(**query)["renewing"][str(days)]
                    listed = self.api.get(
                        "/api/v1/organizations/portfolio/", {**query, "renews_within": days}
                    ).data["count"]
                    self.assertEqual(tile, listed)
        self.assertEqual(self.summary(include_churned="1")["renewing"], {"30": 2, "90": 3})

    def test_empty_book_is_all_zeros(self):
        summary = self.summary(search="nothing matches this")
        self.assertEqual(summary["accounts"], 0)
        self.assertEqual(summary["nps"]["score"], 0)
        self.assertEqual(len(summary["lifecycle"]), len(Customer.LifecycleStage.values))


class FilterOptionsTests(PortfolioFixture):
    def test_options_are_scoped_like_the_rows(self):
        core = Product.objects.create(organisation=self.org, name="Core")
        Product.objects.create(organisation=self.org, name="Unused")
        self.customer("Mine", lifecycle_stage="live", primary_product=core)
        self.customer("Nobody's", owner=None, lifecycle_stage="renewal")
        self.customer("Left", lifecycle_stage="churn", churn_date=self.today)
        self.customer("Hidden", is_archived=True, lifecycle_stage="expansion")
        self.customer("Dana's", owner=self.other, lifecycle_stage="kickoff")

        options = book.filter_options(self.csm)
        self.assertEqual(
            options["owners"],
            [
                {"value": str(self.csm.pk), "name": "Carl CSM"},
                {"value": "unassigned", "name": "Unassigned"},
            ],
        )
        self.assertEqual(
            [row["value"] for row in options["lifecycles"]], ["live", "renewal", "churn"]
        )
        self.assertEqual(options["products"], [{"value": str(core.pk), "name": "Core"}])

        admin_owners = [row["name"] for row in book.filter_options(self.admin)["owners"]]
        self.assertEqual(admin_owners, ["Carl CSM", "Dana CSM", "Unassigned"])

    def test_owners_come_only_from_the_viewers_organisation(self):
        """A bad import or seed row can point an Acme customer at another
        tenant's user; that person's name must never reach an Acme menu."""
        outsider = User.objects.create_user(
            email="gil@globex.io",
            password="supersecret1",
            name="Gil Globex",
            organisation=self.other_org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.customer("Mine")
        self.customer("Misfiled", owner=outsider)
        owners = [row["name"] for row in book.filter_options(self.admin)["owners"]]
        self.assertEqual(owners, ["Carl CSM"])
