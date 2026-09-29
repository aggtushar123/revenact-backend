from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from rest_framework.test import APIClient

from services.accounts.models import User
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.accounts_portfolio.views import AccountPortfolioView
from services.customers.models import Activity, HealthSnapshot, Ticket
from services.customers.tests.test_views import blind_to_one_account
from services.customers.views import AccountListView, AccountStatsView

URL = "/api/v1/accounts/portfolio/"


class PortfolioEndpointTests(AccountPortfolioFixture):
    def get(self, user=None, **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(URL, query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_the_existing_account_routes_still_resolve(self):
        self.assertIs(resolve(URL).func.view_class, AccountPortfolioView)
        self.assertIs(resolve("/api/v1/accounts/").func.view_class, AccountListView)
        self.assertIs(resolve("/api/v1/accounts/stats/").func.view_class, AccountStatsView)

    def test_response_shape(self):
        self.account("Pizza EMEA")
        body = self.get()
        self.assertEqual(
            set(body),
            {"results", "next_cursor", "count", "groups", "summary", "filters", "currency"},
        )
        self.assertEqual((body["currency"], body["count"], body["next_cursor"]), ("USD", 1, None))
        self.assertEqual(
            set(body["results"][0]),
            {
                "id", "name", "initials", "owner", "lifecycle", "health", "renewal", "arr",
                "risk", "pulse", "last_touch_days", "urgent_tickets", "signal",
                "organisation", "extra_organisations", "details",
            },
        )  # fmt: skip
        self.assertEqual(set(body["filters"]), {"organisations", "owners", "lifecycles"})
        self.assertEqual(
            set(body["summary"]),
            {"health", "nps", "lifecycle", "accounts", "arr", "unconverted_count", "renewing"},
        )

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        Ticket.objects.create(
            account=hidden,
            ticket_number="T-H",
            title="Down",
            status=Ticket.Status.OPEN,
            priority=Ticket.Priority.HIGH,
            opened_at=self.today,
        )
        body = self.get(viewer)
        self.assertEqual([row["name"] for row in body["results"]], ["Seen"])
        self.assertEqual(body["results"][0]["urgent_tickets"], 0)
        self.assertEqual(body["summary"]["accounts"], 1)
        self.assertEqual(body["filters"]["owners"], [{"value": str(viewer.pk), "name": "Viewer"}])
        grouped = self.get(viewer, group="lifecycle")
        self.assertEqual(sum(group["count"] for group in grouped["groups"]), 1)
        by_id = self.get(viewer, ids=f"{hidden.pk}")
        self.assertEqual(
            (by_id["count"], by_id["results"], by_id["summary"]["accounts"]), (0, [], 0)
        )
        self.assertEqual(self.get(self.admin)["summary"]["accounts"], 2)

    def test_another_tenants_accounts_never_appear(self):
        globex = self.account("Globex EMEA", customers=[self.globex], owner=None)
        self.assertEqual(self.get(self.admin, ids=str(globex.pk))["count"], 0)
        self.assertEqual(self.get(self.admin)["count"], 0)

    def test_unknown_values_are_ignored_not_rejected(self):
        self.account("A")
        self.account("B")
        body = self.get(
            sort="colour",
            group="product",
            health="purple",
            limit="lots",
            renews_within="7",
            nps="happy",
            cursor="%%%",
            owner="someone",
            organisation="x",
            include_churned="1",
        )
        self.assertEqual(body["count"], 2)

    def test_group_value_serves_one_board_column_with_every_header(self):
        self.account("Live one", lifecycle_stage="live")
        self.account("Live two", lifecycle_stage="live")
        self.account("Onboarding", lifecycle_stage="onboarding")
        body = self.get(group="lifecycle", group_value="live")
        self.assertEqual({row["name"] for row in body["results"]}, {"Live one", "Live two"})
        self.assertEqual(body["count"], 2)
        self.assertEqual([g["key"] for g in body["groups"]], ["onboarding", "live"])
        self.assertEqual(body["summary"]["accounts"], 3)

    def test_following_the_cursor_reads_every_row_once(self):
        for i in range(5):
            self.account(f"Div {i}", arr=Decimal(1000 * (i + 1)))
        seen, cursor = [], None
        for _ in range(10):
            body = self.get(limit="2", **({"cursor": cursor} if cursor else {}))
            seen += [row["name"] for row in body["results"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, ["Div 4", "Div 3", "Div 2", "Div 1", "Div 0"])


class PortfolioQueryCountTests(AccountPortfolioFixture):
    """Every figure is computed over the whole filtered set in a fixed number
    of queries; a per-row query anywhere would make the count grow with the
    book. Ten for an admin, who sees everything (no org-chart walk):
      1. `user.organisation` (`book.load_portfolio`)
      2. the caller's active membership, joined to its organisation and role
         (`services.identity.context.active_membership`, memoised)
      3. `user.role` (`services.identity.context.capabilities_for`)
      4. the accounts — last-touch subquery, owner joined
      5. their health snapshots (`snapshot_history(..., parent="account")`)
      6. the urgent tickets, counted per account
      7. the linked organisations the viewer may open (the M2M table)
      8-10. the filter options: organisations, owners, stages
    If the pinned number is ever wrong, print `[q["sql"] for q in
    ctx.captured_queries]`: every query must be one of these ten kinds; an
    extra one is a bug to fix, not a number to bump."""

    EXPECTED = 10

    def book(self, size):
        for i in range(size):
            account = self.account(
                f"Div {size}-{i}",
                customers=[self.pizza, self.taco],
                renewal_date=self.today + timedelta(days=i),
            )
            HealthSnapshot.objects.create(
                account=account,
                captured_on=self.today - timedelta(days=30),
                health_score=Decimal("6.0"),
            )
            Activity.objects.create(
                account=account, type=Activity.ActivityType.OTHER, occurred_at=self.today
            )
            Ticket.objects.create(
                account=account,
                ticket_number=f"T-{size}-{i}",
                title="Down",
                status=Ticket.Status.OPEN,
                priority=Ticket.Priority.HIGH,
                opened_at=self.today,
            )

    def count(self, user, **query):
        # A fresh user each time, as a real request loads one: memoised lookups
        # cached on a reused instance would otherwise flatter the second call.
        api = APIClient()
        api.force_authenticate(User.objects.get(pk=user.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = api.get(URL, query)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_the_count_is_pinned_and_does_not_grow_with_the_book(self):
        self.book(3)
        small, small_csm = self.count(self.admin), self.count(self.csm)
        self.book(12)
        self.assertEqual(self.count(self.admin), small)
        self.assertEqual(self.count(self.csm), small_csm)
        self.assertEqual(small, self.EXPECTED)

    def test_sorting_grouping_filtering_and_paging_add_no_queries(self):
        self.book(5)
        plain = self.count(self.admin)
        self.assertEqual(self.count(self.admin, sort="-risk", group="owner", limit="2"), plain)
        self.assertEqual(self.count(self.admin, group="lifecycle", group_value="live"), plain)
        self.assertEqual(
            self.count(
                self.admin,
                search="Div",
                health="average,good",
                renews_within="30",
                organisation=str(self.pizza.pk),
            ),
            plain,
        )
