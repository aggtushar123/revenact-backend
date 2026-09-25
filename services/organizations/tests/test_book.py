from datetime import timedelta
from decimal import Decimal

from services.customers.models import Customer, Product
from services.organizations import book
from services.organizations.params import parse_params
from services.organizations.tests.fixtures import PortfolioFixture


class FilteredQuerysetTests(PortfolioFixture):
    def names(self, user=None, **query):
        queryset = book.filtered_queryset(user or self.csm, parse_params(query), today=self.today)
        return set(queryset.values_list("name", flat=True))

    def test_archived_are_hidden(self):
        self.customer("Active")
        self.customer("Archived", is_archived=True)
        self.assertEqual(self.names(), {"Active"})

    def test_churned_are_hidden_unless_asked_for(self):
        self.customer("Live", lifecycle_stage="live")
        self.customer("Left", lifecycle_stage="churn", churn_date=self.today - timedelta(days=10))
        self.customer("Dated", lifecycle_stage="live", churn_date=self.today - timedelta(days=3))
        self.customer("Staged", lifecycle_stage="churn")
        self.assertEqual(self.names(), {"Live"})
        self.assertEqual(self.names(include_churned="1"), {"Live", "Left", "Dated", "Staged"})
        self.assertEqual(self.names(include_churned="yes"), {"Live"})
        self.assertEqual(self.names(lifecycle="churn"), {"Left", "Staged"})
        self.assertEqual(self.names(lifecycle="churn,live"), {"Live", "Left", "Dated", "Staged"})

    def test_ids_name_exactly_those_records_archived_and_churned_included(self):
        live = self.customer("Live")
        archived = self.customer("Archived", is_archived=True)
        left = self.customer(
            "Left", lifecycle_stage="churn", churn_date=self.today - timedelta(days=10)
        )
        self.customer("Unnamed")
        self.assertEqual(
            self.names(ids=f"{live.pk},{archived.pk},{left.pk}"), {"Live", "Archived", "Left"}
        )
        self.assertEqual(self.names(ids=""), set())
        self.assertEqual(self.names(ids="x,y"), set())
        self.assertEqual(self.names(ids=f"{live.pk},zz"), {"Live"})
        self.assertEqual(self.names(ids=",".join(["0"] * 500 + [str(live.pk)])), set())
        # The other filters still apply on top of an id list.
        self.assertEqual(self.names(ids=f"{live.pk}", health="poor"), set())

    def test_other_owners_and_other_tenants_never_appear(self):
        self.customer("Mine")
        self.customer("Nobody's", owner=None)
        danas = self.customer("Dana's", owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        self.assertEqual(self.names(), {"Mine", "Nobody's"})
        self.assertEqual(self.names(ids=f"{danas.pk},{globex.pk}"), set())
        self.assertEqual(self.names(search="Dana"), set())
        self.assertEqual(self.names(user=self.admin), {"Mine", "Nobody's", "Dana's"})

    def test_search_matches_name_or_revenact_id(self):
        pizza = self.customer("Pizza Hut")
        self.customer("Globex")
        self.assertEqual(self.names(search="pIZ"), {"Pizza Hut"})
        self.assertIn("Pizza Hut", self.names(search=str(pizza.pk)))

    def test_owner(self):
        self.customer("Mine")
        self.customer("Nobody's", owner=None)
        self.customer("Dana's", owner=self.other)
        self.assertEqual(self.names(user=self.admin, owner=str(self.other.pk)), {"Dana's"})
        self.assertEqual(self.names(user=self.admin, owner="unassigned"), {"Nobody's"})
        self.assertEqual(
            self.names(user=self.admin, owner="someone"), {"Mine", "Nobody's", "Dana's"}
        )

    def test_lifecycle_is_a_list_and_unknown_stages_are_ignored(self):
        self.customer("Live", lifecycle_stage="live")
        self.customer("Renewing", lifecycle_stage="renewal")
        self.customer("Onboarding", lifecycle_stage="onboarding")
        self.assertEqual(self.names(lifecycle="live,renewal"), {"Live", "Renewing"})
        self.assertEqual(self.names(lifecycle="live,bogus"), {"Live"})
        self.assertEqual(self.names(lifecycle="bogus"), {"Live", "Renewing", "Onboarding"})

    def test_health_bands_match_the_derived_category_at_every_boundary(self):
        for name, score in (("7.0", "7.0"), ("6.9", "6.9"), ("4.0", "4.0"), ("3.9", "3.9")):
            self.customer(name, health_score=Decimal(score))
        self.assertEqual(self.names(health="good"), {"7.0"})
        self.assertEqual(self.names(health="average"), {"6.9", "4.0"})
        self.assertEqual(self.names(health="poor"), {"3.9"})
        self.assertEqual(self.names(health="good,poor"), {"7.0", "3.9"})
        for category in ("good", "average", "poor"):
            for customer in Customer.objects.filter(name__in=self.names(health=category)):
                self.assertEqual(customer.health_category, category)

    def test_product(self):
        core = Product.objects.create(organisation=self.org, name="Core")
        extra = Product.objects.create(organisation=self.org, name="Extra")
        self.customer("On core", primary_product=core)
        self.customer("On extra", primary_product=extra)
        self.customer("On nothing")
        self.assertEqual(self.names(product=str(core.pk)), {"On core"})
        self.assertEqual(self.names(product=f"{core.pk},{extra.pk},x"), {"On core", "On extra"})

    def test_renews_within_includes_overdue(self):
        self.customer("In 30", renewal_date=self.today + timedelta(days=30))
        self.customer("In 31", renewal_date=self.today + timedelta(days=31))
        self.customer("Overdue", renewal_date=self.today - timedelta(days=5))
        self.customer("Undated")
        self.assertEqual(self.names(renews_within="30"), {"In 30", "Overdue"})
        self.assertEqual(self.names(renews_within="90"), {"In 30", "In 31", "Overdue"})
        self.assertEqual(self.names(renews_within="45"), {"In 30", "In 31", "Overdue", "Undated"})

    def test_nps_bands_follow_the_stats_rule(self):
        self.customer("Promoter", nps_score=10)
        self.customer("Passive", nps_score=0)
        self.customer("Detractor", nps_score=-20)
        self.customer("Unscored")
        self.assertEqual(self.names(nps="promoter"), {"Promoter"})
        self.assertEqual(self.names(nps="passive"), {"Passive"})
        self.assertEqual(self.names(nps="detractor"), {"Detractor"})
        self.assertEqual(len(self.names(nps="happy")), 4)
