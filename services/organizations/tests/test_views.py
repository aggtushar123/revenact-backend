from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from services.accounts.models import User
from services.customers.models import Account, Activity, HealthSnapshot, Product, Ticket
from services.organizations.tests.fixtures import PortfolioFixture

URL = "/api/v1/organizations/portfolio/"


class PortfolioEndpointTests(PortfolioFixture):
    def get(self, user=None, **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(URL, query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_response_shape(self):
        self.customer("Pizza Hut")
        body = self.get()
        self.assertEqual(
            set(body),
            {"results", "next_cursor", "count", "groups", "summary", "filters", "currency"},
        )
        self.assertEqual(body["currency"], "USD")
        self.assertEqual(body["count"], 1)
        self.assertIsNone(body["next_cursor"])
        self.assertEqual(
            set(body["results"][0]),
            {
                "id", "name", "initials", "owner", "lifecycle", "health", "renewal", "arr",
                "risk", "pulse", "last_touch_days", "urgent_tickets", "signal",
                "is_archived", "churned", "details",
            },
        )  # fmt: skip
        self.assertEqual(set(body["filters"]), {"owners", "lifecycles", "products"})

    def test_customers_outside_visibility_never_appear_even_by_id(self):
        self.customer("Mine")
        self.customer("Nobody's", owner=None)
        danas = self.customer("Dana's", owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        body = self.get()
        self.assertEqual({row["name"] for row in body["results"]}, {"Mine", "Nobody's"})
        self.assertEqual(body["summary"]["accounts"], 2)
        self.assertNotIn(str(self.other.pk), [o["value"] for o in body["filters"]["owners"]])
        by_id = self.get(ids=f"{danas.pk},{globex.pk}")
        self.assertEqual((by_id["count"], by_id["results"]), (0, []))
        self.assertEqual(by_id["summary"]["accounts"], 0)

    def test_unknown_values_are_ignored_not_rejected(self):
        self.customer("A")
        self.customer("B")
        body = self.get(
            sort="colour",
            group="mood",
            health="purple",
            limit="lots",
            renews_within="7",
            nps="happy",
            cursor="%%%",
            owner="someone",
            product="x",
        )
        self.assertEqual(body["count"], 2)

    def test_groups_and_summary_cover_the_whole_filtered_set(self):
        self.customer("Poor", health_score=Decimal("2.0"))
        self.customer("Average", health_score=Decimal("5.0"))
        self.customer("Good")
        body = self.get(group="health", limit="1")
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["name"], "Poor")
        self.assertEqual(body["count"], 3)
        self.assertEqual(sum(group["count"] for group in body["groups"]), 3)
        self.assertEqual(body["summary"]["accounts"], 3)
        self.assertIsNotNone(body["next_cursor"])

        narrowed = self.get(health="poor,average")
        self.assertEqual(narrowed["summary"]["accounts"], 2)

    def test_group_value_serves_one_board_column_with_all_headers(self):
        self.customer("Live one", lifecycle_stage="live")
        self.customer("Live two", lifecycle_stage="live")
        self.customer("Onboarding", lifecycle_stage="onboarding")
        body = self.get(group="lifecycle", group_value="live")
        self.assertEqual({row["name"] for row in body["results"]}, {"Live one", "Live two"})
        self.assertEqual(body["count"], 2)
        self.assertEqual([g["key"] for g in body["groups"]], ["onboarding", "live"])
        self.assertEqual(body["summary"]["accounts"], 3)

    def test_following_the_cursor_reads_every_row_once(self):
        for i in range(5):
            self.customer(f"Co {i}", arr_billed_at_account=Decimal(1000 * (i + 1)))
        seen, cursor = [], None
        while True:
            query = {"limit": "2", **({"cursor": cursor} if cursor else {})}
            body = self.get(**query)
            seen += [row["name"] for row in body["results"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, ["Co 4", "Co 3", "Co 2", "Co 1", "Co 0"])

    def follow(self, **query):
        seen, cursor = [], None
        for _ in range(20):
            body = self.get(**query, **({"cursor": cursor} if cursor else {}))
            seen += [row["name"] for row in body["results"]]
            cursor = body["next_cursor"]
            if cursor is None:
                return seen
        self.fail(f"the cursor never ended: {seen}")

    def test_following_the_cursor_over_a_grouped_list_ends(self):
        """The final review's probe: a page that ends in a later section must
        not restart the scan inside an earlier one."""
        for i, arr in enumerate((3000, 2000, 1000)):
            self.customer(f"C{i}", health_score=Decimal("2.0"), arr_billed_at_account=arr)
        for i, arr in enumerate((10000, 500), start=3):
            self.customer(f"C{i}", arr_billed_at_account=arr)
        seen = self.follow(group="health", sort="-arr", limit="2")
        self.assertEqual(seen, ["C0", "C1", "C2", "C3", "C4"])


class PortfolioQueryCountTests(PortfolioFixture):
    """Every figure is computed over the whole filtered set in a fixed number
    of queries. A per-row query anywhere would make the count grow with the
    book, which is what the second assertion catches.

    Twelve for an admin, who sees everything (so no org-chart lookups). The
    brief estimated ten for "the caller's membership" as one query; measured
    against this codebase's `services.identity.context` it is three, because
    `load_portfolio` reads `user.organisation` itself (1), `active_membership`
    then does its own select_related fetch of the membership plus its
    organisation and role (2), and `capabilities_for` separately reads
    `user.role` off the column, which is not the membership's role and so is
    never covered by that select_related (3) — all three memoised on the one
    `request.user` instance for the request's lifetime, so they never repeat:
      1. `user.organisation` (`book.load_portfolio`)
      2. the caller's active membership, joined to its organisation and role
         (`services.identity.context.active_membership`, memoised)
      3. `user.role` (`services.identity.context.capabilities_for`)
      4. the customers — touch and open-ticket subqueries, owner / product /
         created_by / modified_by joined
      5. their health snapshots (one prefetch)
      6. the FX rate table
      7. the open High/Critical tickets
      8. those tickets' accounts (prefetch)
      9. those accounts' customers (prefetch)
      10-12. the filter options: owners, stages, products
    If the pinned number is ever wrong, print `[q["sql"] for q in
    ctx.captured_queries]`: every query must be one of these twelve kinds; an
    extra one is a bug to fix, not a number to bump.
    """

    EXPECTED = 12

    def book(self, size):
        product = Product.objects.create(organisation=self.org, name=f"Core {size}")
        for i in range(size):
            customer = self.customer(
                f"Co {size}-{i}",
                primary_product=product,
                renewal_date=self.today + timedelta(days=i),
            )
            HealthSnapshot.objects.create(
                customer=customer,
                captured_on=self.today - timedelta(days=30),
                health_score=Decimal("6.0"),
            )
            Activity.objects.create(
                customer=customer, type=Activity.ActivityType.OTHER, occurred_at=self.today
            )
            account = Account.objects.create(name=f"Div {size}-{i}")
            account.customers.add(customer)
            Ticket.objects.create(
                account=account,
                ticket_number=f"T-{size}-{i}",
                title="Down",
                status=Ticket.Status.OPEN,
                priority=Ticket.Priority.HIGH,
                opened_at=self.today,
            )

    def count(self, **query):
        # A fresh user each time, as a real request loads one: memoised lookups
        # cached on a reused instance would otherwise flatter the second call.
        api = APIClient()
        api.force_authenticate(User.objects.get(pk=self.admin.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = api.get(URL, query)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_the_count_is_pinned_and_does_not_grow_with_the_book(self):
        self.book(3)
        small = self.count()
        self.book(12)
        self.assertEqual(self.count(), small)
        self.assertEqual(small, self.EXPECTED)

    def test_sorting_grouping_filtering_and_paging_add_no_queries(self):
        self.book(5)
        plain = self.count()
        self.assertEqual(self.count(sort="-risk", group="owner", limit="2"), plain)
        self.assertEqual(self.count(group="lifecycle", group_value="live"), plain)
        self.assertEqual(self.count(search="Co", health="average,good", renews_within="30"), plain)
