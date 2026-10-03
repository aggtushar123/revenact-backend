"""A segment's members as the kind's own list reads them, the tiles over
them, the CSV, the builder's preview, and pinning or keeping out. Twice
filtered throughout: a shared viewer gets their own members and a count."""

import csv
import io
import json
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts.models import User
from services.accounts_portfolio.fields import FIELDS as ACCOUNT_FIELDS
from services.customers.models import (
    Account,
    Activity,
    Contact,
    Customer,
    HealthSnapshot,
    Ticket,
)
from services.customers.tests.test_views import blind_to_one_account
from services.fx_rates.models import FxRate
from services.organizations.fields import FIELDS as ORGANISATION_FIELDS
from services.segments.export import CONTACT_FIELDS
from services.segments.models import MAX_PINNED, SegmentChange
from services.segments.tests.fixtures import SegmentFixture, rule

URL = "/api/v1/segments/"
PIN_LIMIT = "A segment can pin at most 500 records, and keep out as many."
HEALTHY = rule("health_score", "gt", 0)
ACTIVE = rule("status", "is", "active")
LISTING_KEYS = {
    "results", "next_cursor", "count", "groups", "kind", "currency", "summary", "hidden_count",
}  # fmt: skip


class MembersFixture(SegmentFixture):
    """Pizza Hut bills 12,000 USD with CSAT 80; Taco Bell bills 10,000 EUR
    (15,000 USD) with CSAT 40. Pizza EMEA and Taco West carry 5,000 and 7,000
    ARR. Sam and Uma are Pizza Hut's people, Tom is Taco Bell's."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        FxRate.objects.create(
            organisation=cls.org, currency="EUR", rate_to_org_currency=Decimal("1.5")
        )
        Customer.objects.filter(pk=cls.pizza.pk).update(
            arr_billed_at_account=Decimal("12000"), csat_score=Decimal("80")
        )
        Customer.objects.filter(pk=cls.taco.pk).update(
            arr_billed_at_account=Decimal("10000"), currency="EUR", csat_score=Decimal("40")
        )
        Account.objects.filter(pk=cls.emea.pk).update(
            arr=Decimal("5000"), health_score=Decimal("6.0"), csat_score=Decimal("70")
        )
        Account.objects.filter(pk=cls.west.pk).update(
            arr=Decimal("7000"), health_score=Decimal("4.0")
        )
        cls.sam = Contact.objects.create(customer=cls.pizza, name="Sam", email="sam@pizza.io")
        cls.uma = Contact.objects.create(account=cls.emea, name="Uma", email="uma@pizza.io")
        cls.tom = Contact.objects.create(customer=cls.taco, name="Tom", email="tom@taco.io")

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def members(self, user, segment, **query):
        response = self.api(user).get(f"{URL}{segment.pk}/members/", query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def ids(self, body):
        return sorted(row["id"] for row in body["results"])


class RowTests(MembersFixture):
    def test_organisation_members_are_the_organizations_lists_own_rows(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        body = self.members(self.admin, segment)
        self.assertEqual(set(body), LISTING_KEYS)
        self.assertEqual(
            (body["kind"], body["currency"], body["hidden_count"]), ("customer", "USD", 0)
        )
        portfolio = self.api(self.admin).get("/api/v1/organizations/portfolio/").data["results"]
        self.assertEqual(body["results"], [row for row in portfolio if row["id"] in self.ids(body)])

    def test_account_members_are_the_accounts_lists_own_rows(self):
        segment = self.segment(owner=self.admin, kind="account", rules=HEALTHY)
        body = self.members(self.admin, segment)
        portfolio = self.api(self.admin).get("/api/v1/accounts/portfolio/").data["results"]
        self.assertEqual(self.ids(body), sorted([self.emea.pk, self.west.pk]))
        self.assertEqual(body["results"], [row for row in portfolio if row["id"] in self.ids(body)])

    def test_contact_members_are_the_contacts_lists_own_rows(self):
        segment = self.segment(owner=self.admin, kind="contact", rules=ACTIVE)
        body = self.members(self.admin, segment)
        listed = self.api(self.admin).get("/api/v1/contacts/").data["results"]
        self.assertEqual([row["name"] for row in body["results"]], ["Sam", "Tom", "Uma"])
        self.assertEqual(
            body["results"], sorted(listed, key=lambda row: (row["name"].casefold(), row["id"]))
        )

    def test_sort_group_and_cursor_are_the_lists_own(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        first = self.members(self.admin, segment, sort="name", limit="1")
        self.assertEqual([row["name"] for row in first["results"]], ["Pizza Hut"])
        self.assertIsInstance(first["next_cursor"], str)
        second = self.members(
            self.admin, segment, sort="name", limit="1", cursor=first["next_cursor"]
        )
        self.assertEqual(
            ([r["name"] for r in second["results"]], second["next_cursor"]), (["Taco Bell"], None)
        )
        grouped = self.members(self.admin, segment, group="health")
        self.assertEqual([group["key"] for group in grouped["groups"]], ["poor", "good"])

    def test_contacts_page_by_a_cursor_too(self):
        segment = self.segment(owner=self.admin, kind="contact", rules=ACTIVE)
        first = self.members(self.admin, segment, limit="2")
        second = self.members(self.admin, segment, limit="2", cursor=first["next_cursor"])
        self.assertEqual([row["name"] for row in first["results"]], ["Sam", "Tom"])
        self.assertEqual(
            ([row["name"] for row in second["results"]], second["next_cursor"]), (["Uma"], None)
        )

    def test_the_lists_own_filters_do_not_narrow_members(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        body = self.members(
            self.admin, segment, owner=str(self.csm.pk), health="poor", ids=str(self.pizza.pk)
        )
        self.assertEqual(self.ids(body), sorted([self.pizza.pk, self.taco.pk]))
        people = self.segment(owner=self.admin, kind="contact", rules=ACTIVE, name="People")
        body = self.members(self.admin, people, customer=str(self.taco.pk), role="champion")
        self.assertEqual([row["name"] for row in body["results"]], ["Sam", "Tom", "Uma"])

    def test_search_narrows_the_rows_not_the_tiles(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        body = self.members(self.admin, segment, search="pizza")
        self.assertEqual(
            ([row["name"] for row in body["results"]], body["count"]), (["Pizza Hut"], 1)
        )
        self.assertEqual(body["summary"]["members"], 2)


class SummaryTests(MembersFixture):
    def test_the_tiles_equal_the_members_they_cover(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        body = self.members(self.admin, segment, limit="100")
        rows, summary = body["results"], body["summary"]
        self.assertEqual(summary["members"], body["count"])
        self.assertEqual(summary["arr"], sum(row["arr"] for row in rows))
        self.assertEqual(summary["arr"], 27000.0)
        self.assertEqual(summary["avg_health"], 5.5)
        self.assertEqual(summary["avg_csat"], 60.0)
        self.assertEqual((summary["unconverted_count"], summary["currency"]), (0, "USD"))

    def test_accounts_tiles(self):
        segment = self.segment(owner=self.admin, kind="account", rules=HEALTHY)
        summary = self.members(self.admin, segment)["summary"]
        self.assertEqual(
            (summary["members"], summary["arr"], summary["avg_health"], summary["avg_csat"]),
            (2, 12000.0, 5.0, 70.0),
        )

    def test_contacts_tiles(self):
        segment = self.segment(owner=self.admin, kind="contact", rules=ACTIVE)
        summary = self.members(self.admin, segment)["summary"]
        self.assertEqual((summary["members"], summary["contacts"]["total"]), (3, 3))
        self.assertEqual(
            (summary["arr"], summary["avg_health"], summary["avg_csat"]), (None, None, None)
        )

    def test_money_with_no_rate_is_counted_not_summed(self):
        Customer.objects.create(
            organisation=self.org, name="Yen Co", currency="JPY", owner=self.admin,
            arr_billed_at_account=Decimal("1000000"), health_score=Decimal("5.0"),
        )  # fmt: skip
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        summary = self.members(self.admin, segment)["summary"]
        self.assertEqual(
            (summary["members"], summary["arr"], summary["unconverted_count"]), (3, 27000.0, 1)
        )

    def test_entries_and_exits_count_only_what_the_viewer_may_open(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY, sharing="workspace")
        for record, change, days_ago in (
            (self.pizza.pk, "entered", 0),
            (self.taco.pk, "left", 2),
            (self.pizza.pk, "left", 10),
        ):
            SegmentChange.objects.create(
                segment=segment, record_id=record, change=change,
                changed_on=self.today - timedelta(days=days_ago),
            )  # fmt: skip
        admin = self.members(self.admin, segment)["summary"]
        carl = self.members(self.csm, segment)["summary"]
        self.assertEqual((admin["entered_7d"], admin["left_7d"]), (1, 1))
        self.assertEqual((carl["entered_7d"], carl["left_7d"]), (1, 0))


class PrivacyTests(MembersFixture):
    def test_a_shared_viewer_gets_their_own_members_and_a_count(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY, sharing="workspace")
        body = self.members(self.csm, segment)
        self.assertEqual((self.ids(body), body["hidden_count"]), ([self.pizza.pk], 1))
        self.assertEqual(body["summary"]["members"], 1)
        self.assertNotIn("Taco Bell", json.dumps(body, default=str))

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        segment = self.segment(owner=self.admin, kind="account", rules=HEALTHY, sharing="workspace")
        body = self.members(viewer, segment)
        self.assertEqual(self.ids(body), [seen.pk])
        self.assertEqual(body["hidden_count"], 3)
        response = self.api(viewer).get(f"{URL}{segment.pk}/members/export.csv")
        names = [row[0] for row in csv.reader(io.StringIO(response.content.decode()))][1:]
        self.assertEqual(names, ["Seen"])

    def test_another_tenant_and_a_missing_segment_read_the_same(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY, sharing="workspace")
        stranger = self.api(self.stranger)
        for path in ("members/", "members/export.csv"):
            hidden = stranger.get(f"{URL}{segment.pk}/{path}")
            missing = stranger.get(f"{URL}999999/{path}")
            self.assertEqual((hidden.status_code, hidden.content), (404, missing.content))


class ExportTests(MembersFixture):
    def export(self, user, segment, **query):
        response = self.api(user).get(f"{URL}{segment.pk}/members/export.csv", query)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        return list(csv.reader(io.StringIO(response.content.decode()))), response

    def test_organisations_export_the_organizations_columns_and_is_audited(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        rows, response = self.export(self.admin, segment, sort="name", owner=str(self.csm.pk))
        self.assertEqual(rows[0], [field.label for field in ORGANISATION_FIELDS] + ["Currency"])
        self.assertEqual([row[0] for row in rows[1:]], ["Pizza Hut", "Taco Bell"])
        self.assertIn(f'filename="segment-{segment.pk}-', response["Content-Disposition"])
        event = AuditEvent.objects.get(action="segment.exported")
        self.assertEqual(
            (event.target_id, event.metadata), (str(segment.pk), {"count": 2, "params": ["sort"]})
        )

    def test_accounts_export_the_accounts_columns(self):
        segment = self.segment(owner=self.admin, kind="account", rules=HEALTHY)
        rows, _response = self.export(self.admin, segment)
        self.assertEqual(rows[0], [field.label for field in ACCOUNT_FIELDS] + ["Currency"])
        self.assertEqual(len(rows), 3)

    def test_contacts_export_their_own_columns(self):
        segment = self.segment(owner=self.admin, kind="contact", rules=ACTIVE)
        rows, _response = self.export(self.admin, segment)
        self.assertEqual(
            rows[0],
            [
                "Name", "Email", "Phone", "Role", "Status", "Sentiment", "Language",
                "Last Contacted", "Organisation", "Account",
            ],
        )  # fmt: skip
        self.assertEqual(rows[0], [field.label for field in CONTACT_FIELDS])
        uma = next(row for row in rows if row[0] == "Uma")
        self.assertEqual((uma[-2], uma[-1]), ("Pizza Hut", "Pizza EMEA"))

    def test_a_shared_viewer_exports_only_what_they_may_open(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY, sharing="workspace")
        rows, _response = self.export(self.other, segment)
        self.assertEqual([row[0] for row in rows[1:]], ["Taco Bell"])


class PinTests(MembersFixture):
    def mark(self, user, segment, record_id, state):
        return self.api(user).patch(
            f"{URL}{segment.pk}/members/{record_id}/", {"state": state}, format="json"
        )

    def events(self):
        rows = AuditEvent.objects.filter(action__startswith="segment.member_").order_by("pk")
        return [(row.action, row.metadata) for row in rows]

    def test_the_owner_pins_keeps_out_and_clears(self):
        segment = self.segment(owner=self.admin, rules=rule("health_score", "gt", 5))
        self.assertEqual(self.ids(self.members(self.admin, segment)), [self.pizza.pk])

        response = self.mark(self.admin, segment, self.taco.pk, "pinned")
        self.assertEqual(response.data, {"pinned_ids": [self.taco.pk], "excluded_ids": []})
        self.assertEqual(
            self.ids(self.members(self.admin, segment)), sorted([self.pizza.pk, self.taco.pk])
        )
        segment.refresh_from_db()
        self.assertEqual(segment.last_members, sorted([self.pizza.pk, self.taco.pk]))

        response = self.mark(self.admin, segment, self.taco.pk, "excluded")
        self.assertEqual(response.data, {"pinned_ids": [], "excluded_ids": [self.taco.pk]})
        self.mark(self.admin, segment, self.taco.pk, "none")
        self.mark(self.admin, segment, self.taco.pk, "none")
        record = self.taco.pk
        self.assertEqual(
            self.events(),
            [
                ("segment.member_pinned", {"record_id": record, "pinned": True}),
                ("segment.member_pinned", {"record_id": record, "pinned": False}),
                ("segment.member_excluded", {"record_id": record, "excluded": True}),
                ("segment.member_excluded", {"record_id": record, "excluded": False}),
            ],
        )

    def test_only_a_record_the_owner_may_open_and_missing_reads_the_same(self):
        segment = self.segment(owner=self.csm, rules=HEALTHY)
        hidden = self.mark(self.csm, segment, self.taco.pk, "pinned")
        missing = self.mark(self.csm, segment, 999999, "pinned")
        self.assertEqual((hidden.status_code, hidden.data), (404, missing.data))
        segment.refresh_from_db()
        self.assertEqual(segment.pinned_ids, [])

    def test_non_owners_cannot_pin(self):
        shared = self.segment(owner=self.csm, sharing="workspace")
        private = self.segment(owner=self.csm, name="Private")
        self.assertEqual(self.mark(self.other, shared, self.taco.pk, "pinned").status_code, 403)
        self.assertEqual(self.mark(self.other, private, self.taco.pk, "pinned").status_code, 404)

    def test_a_bad_state_and_both_limits(self):
        segment = self.segment(owner=self.admin, pinned_ids=list(range(10**6, 10**6 + MAX_PINNED)))
        self.assertEqual(self.mark(self.admin, segment, self.pizza.pk, "starred").status_code, 400)
        response = self.mark(self.admin, segment, self.pizza.pk, "pinned")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {"detail": "A segment can pin at most 500 records, and keep out as many."},
        )
        full = self.segment(
            owner=self.admin, name="Full", excluded_ids=list(range(10**6, 10**6 + MAX_PINNED))
        )
        response = self.mark(self.admin, full, self.pizza.pk, "excluded")
        self.assertEqual((response.status_code, response.data), (400, {"detail": PIN_LIMIT}))
        self.assertEqual(self.mark(self.admin, full, self.pizza.pk, "pinned").status_code, 200)


class PreviewTests(MembersFixture):
    def preview(self, user, **body):
        return self.api(user).post(f"{URL}preview/", body, format="json")

    def test_the_count_the_first_ten_and_the_totals(self):
        Customer.objects.bulk_create(
            Customer(organisation=self.org, name=f"Extra {i:02}", owner=self.admin)
            for i in range(11)
        )
        response = self.preview(self.admin, kind="customer", rules=HEALTHY)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.data
        self.assertEqual(set(body), {"kind", "count", "results", "summary"})
        self.assertEqual(body["count"], 13)
        self.assertEqual(
            [row["name"] for row in body["results"]], [f"Extra {i:02}" for i in range(10)]
        )
        self.assertEqual(set(body["results"][0]), {"id", "name", "owner", "health"})
        self.assertEqual((body["summary"]["members"], body["summary"]["entered_7d"]), (13, None))

    def test_contacts_preview_names_their_parent(self):
        body = self.preview(self.csm, kind="contact", rules=ACTIVE).data
        self.assertEqual(
            body["results"],
            [
                {
                    "id": self.sam.pk,
                    "name": "Sam",
                    "role": "Other",
                    "parent": {"kind": "customer", "id": self.pizza.pk, "name": "Pizza Hut"},
                },
                {
                    "id": self.uma.pk,
                    "name": "Uma",
                    "role": "Other",
                    "parent": {"kind": "account", "id": self.emea.pk, "name": "Pizza EMEA"},
                },
            ],
        )

    def test_a_rule_naming_a_hidden_record_is_refused_like_a_missing_one(self):
        hidden = self.preview(
            self.csm, kind="account", rules=rule("organisation", "is", self.taco.pk)
        )
        missing = self.preview(self.csm, kind="account", rules=rule("organisation", "is", 999999))
        self.assertEqual((hidden.status_code, hidden.data), (400, missing.data))

    def test_a_pin_the_caller_cannot_open_is_not_shown(self):
        body = self.preview(
            self.csm,
            kind="customer",
            rules=rule("health_score", "gt", 100),
            pinned_ids=[self.taco.pk],
        ).data
        self.assertEqual((body["count"], body["results"]), (0, []))

    def test_the_query_count_is_pinned(self):
        """Six queries for an organisations preview by an admin, whatever the
        book's size: the caller's active membership and role (capabilities),
        the first ten members (owner joined), `user.organisation` (the ARR
        tile's mapping and currency), the FX rates and the tiles (one
        aggregate)."""
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=self.admin.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = client.post(
                f"{URL}preview/", {"kind": "customer", "rules": HEALTHY}, format="json"
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(ctx.captured_queries), 6)


class MembersQueryCountTests(MembersFixture):
    """`GET /segments/<id>/members/` for an organisations segment runs a fixed
    number of queries, whatever its size. Twelve for its admin owner:
      1. the segment (owner joined)
      2. `user.organisation` (`customers.scoping.visible_customers`)
      3. the caller's active membership (`identity.context.active_membership`)
      4. `user.role` (`identity.context.capabilities_for`)
      5. the customers: the members as a subquery, touch and ticket
         subqueries, people and product joined
      6. their health snapshots
      7. the FX rate table
      8. the open High/Critical tickets
      9. those tickets' accounts (prefetch)
      10. those accounts' customers (prefetch)
      11. the tiles (one aggregate, reusing the FX rates already read)
      12. the last 7 days' entries and exits the caller may open
    A CSM adds one: their reports (`reports_to`, the org chart). A reader who
    is not the owner adds five: the owner's organisation, membership, role
    and reports, and the hidden count (the owner is another person); that is
    flat too. If the pinned number is ever
    wrong, print `[q["sql"] for q in ctx.captured_queries]`: every query must
    be one of these kinds; an extra one is a bug to fix, not a number to bump."""

    EXPECTED = 12

    def book(self, size):
        for i in range(size):
            customer = Customer.objects.create(
                organisation=self.org, name=f"Co {size}-{i}", owner=self.csm,
                health_score=Decimal("6.0"), renewal_date=self.today + timedelta(days=i),
            )  # fmt: skip
            HealthSnapshot.objects.create(
                customer=customer,
                captured_on=self.today - timedelta(days=30),
                health_score=Decimal("5.0"),
            )
            Activity.objects.create(
                customer=customer, type=Activity.ActivityType.OTHER, occurred_at=self.today
            )
            account = Account.objects.create(name=f"Div {size}-{i}", owner=self.csm)
            account.customers.add(customer)
            Ticket.objects.create(
                account=account, ticket_number=f"T-{size}-{i}", title="Down",
                status=Ticket.Status.OPEN, priority=Ticket.Priority.HIGH, opened_at=self.today,
            )  # fmt: skip
            Contact.objects.create(customer=customer, name=f"Org person {size}-{i}")
            Contact.objects.create(account=account, name=f"Account person {size}-{i}")

    def count(self, user, segment):
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=user.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(f"{URL}{segment.pk}/members/")
        self.assertEqual(response.status_code, 200, response.content)
        return len(ctx.captured_queries)

    def test_pinned_and_flat_as_the_segment_grows(self):
        admins = self.segment(owner=self.admin, rules=HEALTHY)
        carls = self.segment(owner=self.csm, rules=HEALTHY, name="Carl's")
        self.book(3)
        small, small_csm = self.count(self.admin, admins), self.count(self.csm, carls)
        self.book(12)
        self.assertEqual(self.count(self.admin, admins), small)
        self.assertEqual(self.count(self.csm, carls), small_csm)
        self.assertEqual(small, self.EXPECTED)

    def test_a_shared_viewer_is_flat_too(self):
        segment = self.segment(owner=self.csm, rules=HEALTHY, sharing="workspace")
        self.book(3)
        small = self.count(self.admin, segment)
        self.book(12)
        self.assertEqual(self.count(self.admin, segment), small)

    def test_accounts_members_are_pinned_and_flat(self):
        """Ten for an accounts segment read by its admin owner: the segment,
        `user.organisation`, membership and role (as above), the accounts
        (members as a subquery), their health snapshots, their open-ticket
        counts, their linked organisations, the tiles (one aggregate) and
        the entries and exits. A CSM adds their reports."""
        admins = self.segment(owner=self.admin, kind="account", rules=HEALTHY)
        carls = self.segment(owner=self.csm, kind="account", rules=HEALTHY, name="Carl's")
        self.book(3)
        small, small_csm = self.count(self.admin, admins), self.count(self.csm, carls)
        self.book(12)
        self.assertEqual(self.count(self.admin, admins), small)
        self.assertEqual(self.count(self.csm, carls), small_csm)
        self.assertEqual((small, small_csm), (10, 11))

    def test_contacts_members_are_pinned_and_flat(self):
        """Ten for a contacts segment read by its admin owner: the segment,
        `user.organisation`, membership and role (as above), every member's
        id and name (the keyset), the page's contacts, their accounts'
        organisations (prefetch), the organisations the viewer may open (the
        serializer names only those), the tiles (`contacts_summary`) and the
        entries and exits. A CSM adds their reports."""
        admins = self.segment(owner=self.admin, kind="contact", rules=ACTIVE)
        carls = self.segment(owner=self.csm, kind="contact", rules=ACTIVE, name="Carl's")
        self.book(3)
        small, small_csm = self.count(self.admin, admins), self.count(self.csm, carls)
        self.book(12)
        self.assertEqual(self.count(self.admin, admins), small)
        self.assertEqual(self.count(self.csm, carls), small_csm)
        self.assertEqual((small, small_csm), (10, 11))


class AuthenticationTests(MembersFixture):
    def test_every_route_needs_a_signed_in_caller(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        client = APIClient()
        for method, path in (
            ("get", f"{URL}{segment.pk}/members/"),
            ("get", f"{URL}{segment.pk}/members/export.csv"),
            ("patch", f"{URL}{segment.pk}/members/{self.pizza.pk}/"),
            ("post", f"{URL}preview/"),
        ):
            with self.subTest(path=path):
                self.assertEqual(getattr(client, method)(path).status_code, 401)
