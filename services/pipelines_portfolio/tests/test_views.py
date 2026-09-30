import json
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from rest_framework.test import APIClient

from services.accounts.models import User
from services.customers.tests.test_views import blind_to_one_account
from services.customers.views import OpportunityListView, RiskListView
from services.pipelines_portfolio.kinds import OPPORTUNITIES
from services.pipelines_portfolio.tests.fixtures import PipelineFixture
from services.pipelines_portfolio.views import PipelineView

URL = "/api/v1/pipelines/{}/"
EVERY_STAGE = ",".join(OPPORTUNITIES.stages)


class PipelineEndpointTests(PipelineFixture):
    def get(self, user=None, kind="opportunities", **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(URL.format(kind), query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def titles(self, body):
        return [row["title"] for row in body["results"]]

    def test_requires_authentication(self):
        for kind in ("opportunities", "risks"):
            self.assertEqual(APIClient().get(URL.format(kind)).status_code, 401)

    def test_routes(self):
        self.assertIs(resolve(URL.format("opportunities")).func.view_class, PipelineView)
        self.assertIs(resolve(URL.format("risks")).func.view_class, PipelineView)
        self.assertIs(resolve("/api/v1/opportunities/").func.view_class, OpportunityListView)
        self.assertIs(resolve("/api/v1/risks/").func.view_class, RiskListView)
        api = APIClient()
        api.force_authenticate(self.csm)
        self.assertEqual(api.get(URL.format("deals")).status_code, 404)

    def test_response_shape(self):
        self.opportunity("Upsell")
        body = self.get()
        self.assertEqual(
            set(body),
            {"kind", "results", "next_cursor", "count", "groups", "summary", "filters", "currency"},
        )
        self.assertEqual(
            set(body["results"][0]),
            {
                "id", "kind", "title", "parent", "companies", "owner", "mrr", "stage",
                "priority", "department", "date", "open", "overdue", "signal",
                "stage_changed_at", "created_at",
            },
        )  # fmt: skip
        self.assertEqual(
            set(body["summary"]),
            {"items", "mrr", "open", "within", "overdue", "done_this_quarter", "stages"},
        )
        self.assertEqual(
            set(body["filters"]),
            {"organisations", "accounts", "owners", "stages", "priorities", "departments"},
        )

    def test_risks_are_their_own_book(self):
        self.opportunity("Upsell")
        self.risk("Budget")
        self.risk("Handled", stage="mitigated")
        body = self.get(kind="risks")
        self.assertEqual((body["kind"], self.titles(body)), ("risks", ["Budget"]))
        self.assertEqual(body["summary"]["items"], 2)

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.opportunity("On seen", account=seen)
        on_hidden = self.opportunity("On hidden", account=hidden, mrr=Decimal("9000"))
        body = self.get(viewer, group="parent")
        self.assertEqual(self.titles(body), ["On seen"])
        self.assertEqual((body["summary"]["items"], body["summary"]["mrr"]), (1, 1000.0))
        self.assertEqual([group["label"] for group in body["groups"]], ["Seen"])
        self.assertEqual([option["name"] for option in body["filters"]["accounts"]], ["Seen"])
        self.assertEqual(self.get(viewer, ids=str(on_hidden.pk))["count"], 0)
        self.assertEqual(self.get(viewer, account=str(hidden.pk))["summary"]["items"], 0)
        self.assertEqual(self.get(self.admin)["summary"]["items"], 2)

    def test_another_tenant_never_appears(self):
        theirs = self.opportunity("Globex deal", customer=self.globex, department="")
        self.assertEqual(self.get(self.admin, ids=str(theirs.pk))["count"], 0)
        self.assertEqual(self.get(self.admin)["summary"]["items"], 0)
        self.assertEqual(self.get(self.admin, organisation=str(self.globex.pk))["count"], 0)

    def test_another_tenants_item_reads_the_same_as_one_that_does_not_exist(self):
        theirs = self.opportunity("Globex deal", customer=self.globex, department="")
        self.opportunity("Ours")
        missing = theirs.pk + 1000
        for user in (self.admin, self.csm):
            self.assertEqual(self.get(user, ids=str(theirs.pk)), self.get(user, ids=str(missing)))
            self.assertEqual(
                self.get(user, organisation=str(self.globex.pk)),
                self.get(user, organisation=str(self.globex.pk + 1000)),
            )

    def test_the_department_rule(self):
        self.opportunity("Sales'", department=User.Function.SALES)
        self.opportunity("Ours")
        self.assertEqual(self.titles(self.get()), ["Ours"])
        self.assertEqual(sorted(self.titles(self.get(self.admin))), ["Ours", "Sales'"])

    def test_the_department_rule_holds_under_an_ids_filter(self):
        theirs = self.opportunity("Sales'", department=User.Function.SALES)
        body = self.get(ids=str(theirs.pk), department="sales")
        self.assertEqual((body["count"], body["summary"]["items"]), (0, 0))

    def test_an_organisation_filter_the_viewer_cannot_open_names_nothing(self):
        self.opportunity("On Taco Bell", customer=self.taco)
        self.opportunity("Mine")
        body = self.get(organisation=str(self.taco.pk), group="stage")
        self.assertEqual((body["count"], body["summary"]["items"], body["groups"]), (0, 0, []))

    def test_an_account_filter_the_viewer_cannot_open_names_nothing(self):
        theirs = self.account("Taco only", customers=[self.taco], owner=self.other)
        self.opportunity("On Taco account", account=theirs)
        self.opportunity("Mine")
        body = self.get(account=str(theirs.pk), group="stage")
        self.assertEqual((body["count"], body["summary"]["items"], body["groups"]), (0, 0, []))

    def test_companies_are_trimmed_on_rows(self):
        shared = self.account("Shared", customers=[self.pizza, self.taco], owner=self.other)
        self.opportunity("Seats", account=shared)
        [row] = self.get()["results"]
        self.assertEqual(row["companies"], [{"id": self.pizza.pk, "name": "Pizza Hut"}])

    def test_no_part_of_the_body_names_a_hidden_organisation(self):
        # Dana owns the account and Taco Bell: Carl reads the account through
        # Pizza Hut but may not open Taco Bell.
        shared = self.account("Shared", customers=[self.pizza, self.taco], owner=self.other)
        self.opportunity("Seats", account=shared)
        self.risk("Exposure", account=shared)
        self.opportunity("Hidden deal", customer=self.taco)
        for kind in ("opportunities", "risks"):
            for group in ("", "parent", "owner"):
                body = self.get(kind=kind, group=group)
                self.assertTrue(body["results"])
                self.assertNotIn("Taco Bell", json.dumps(body))
                organisations = [option["value"] for option in body["filters"]["organisations"]]
                self.assertEqual(organisations, [str(self.pizza.pk)])

    def test_another_tenants_person_is_never_named(self):
        stranger = User.objects.create_user(
            email="gus@globex.io",
            password="supersecret1",
            name="Gus Globex",
            organisation=self.other_org,
            role=User.Role.CSM,
        )
        imported = self.account("Imported", owner=stranger)
        self.opportunity("Bad import", account=imported)
        for user in (self.admin, self.csm):
            for group in ("", "owner"):
                body = self.get(user, group=group)
                self.assertEqual(self.titles(body), ["Bad import"])
                self.assertNotIn("Gus Globex", json.dumps(body))
                self.assertEqual(body["results"][0]["owner"]["id"], None)

    def test_an_owner_filter_naming_another_tenants_person_narrows_to_nothing(self):
        stranger = User.objects.create_user(
            email="gus@globex.io",
            password="supersecret1",
            name="Gus Globex",
            organisation=self.other_org,
            role=User.Role.CSM,
        )
        imported = self.account("Imported", owner=stranger)
        self.opportunity("Bad import", account=imported)
        self.opportunity("Mine")
        body = self.get(self.admin, owner=str(stranger.pk))
        self.assertEqual((body["count"], body["summary"]["items"]), (0, 0))

    def test_unknown_values_are_ignored_not_rejected(self):
        self.opportunity("A")
        self.opportunity("B")
        body = self.get(
            sort="colour",
            group="health",
            stage="won",
            priority="urgent",
            department="pirates",
            date="7",
            changed="year",
            limit="lots",
            cursor="%%%",
            owner="someone",
            organisation="x",
            account="y",
        )
        self.assertEqual(body["count"], 2)

    def test_the_board_asks_for_every_stage_one_column_at_a_time(self):
        self.opportunity("N1", stage="negotiation")
        self.opportunity("Won", stage="closed_won")
        body = self.get(group="stage", stage=EVERY_STAGE, group_value="closed_won")
        self.assertEqual(self.titles(body), ["Won"])
        self.assertEqual([group["key"] for group in body["groups"]], ["negotiation", "closed_won"])

    def test_following_the_cursor_reads_every_row_once(self):
        for i in range(5):
            self.opportunity(f"Deal {i}", mrr=Decimal(1000 * (i + 1)))
        seen, cursor = [], None
        for _ in range(10):
            body = self.get(limit="2", **({"cursor": cursor} if cursor else {}))
            seen += self.titles(body)
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, ["Deal 4", "Deal 3", "Deal 2", "Deal 1", "Deal 0"])


class PipelineQueryCountTests(PipelineFixture):
    """Every figure is computed over the whole filtered set in a fixed number
    of queries; a per-row query anywhere would make the count grow with the
    book. Eight for an admin, who sees everything (no org-chart walk):
      1. `user.organisation` (`book.load_book`)
      2. the caller's active membership, joined to its organisation and role
         (`services.identity.context.active_membership`, memoised)
      3. `user.role` (`services.identity.context.capabilities_for`)
      4. the items, both parents and their owners joined
      5. the openable organisations of the account-level items (skipped when
         the filtered set has none — every book below has some)
      6-8. the filter options: organisations, accounts, owners and departments
    Nine for a CSM (measured): the same, plus one step of the org-chart walk
    (`accounts_user WHERE reports_to_id IN (…)`) — one query per level of
    reports under the viewer, so it grows with the org chart's depth, never
    with the book. Carl has no reports: one level, one query.
    If the pinned number is ever wrong, print `[q["sql"] for q in
    ctx.captured_queries]`: every query must be one of these kinds. Only a
    miscount of the fixed identity lookups (1-3) may change `EXPECTED`; any
    other extra query is a bug to fix, not a number to bump."""

    EXPECTED = 8
    EXPECTED_CSM = 9

    def book(self, size):
        for i in range(size):
            account = self.account(f"Div {size}-{i}", customers=[self.pizza, self.taco])
            self.opportunity(f"Org deal {size}-{i}", expected_close=self.days(i))
            self.opportunity(f"Account deal {size}-{i}", account=account, stage="closed_won")
            self.risk(f"Risk {size}-{i}", account=account)

    def count(self, user, kind="opportunities", **query):
        # A fresh user each time, as a real request loads one: memoised lookups
        # cached on a reused instance would otherwise flatter the second call.
        api = APIClient()
        api.force_authenticate(User.objects.get(pk=user.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = api.get(URL.format(kind), query)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_the_count_is_pinned_and_does_not_grow_with_the_book(self):
        self.book(3)
        small = self.count(self.admin)
        small_csm = self.count(self.csm)
        small_risks = self.count(self.admin, "risks")
        self.book(12)
        self.assertEqual(self.count(self.admin), small)
        self.assertEqual(self.count(self.csm), small_csm)
        self.assertEqual(self.count(self.admin, "risks"), small_risks)
        self.assertEqual((small, small_risks), (self.EXPECTED, self.EXPECTED))
        self.assertEqual(small_csm, self.EXPECTED_CSM)

    def test_sorting_grouping_filtering_and_paging_add_no_queries(self):
        self.book(5)
        plain = self.count(self.admin)
        self.assertEqual(self.count(self.admin, sort="-date", group="month", limit="2"), plain)
        self.assertEqual(
            self.count(self.admin, group="stage", stage=EVERY_STAGE, group_value="closed_won"),
            plain,
        )
        self.assertEqual(
            self.count(
                self.admin,
                search="Account",
                organisation=str(self.pizza.pk),
                owner=str(self.csm.pk),
                priority="medium",
                department="cs,none",
                changed="quarter",
            ),
            plain,
        )
