"""GET /api/v1/contacts/ — the Contacts page's list: its filters, the
organisation and account on every row, a summary over the whole filtered
set, and a query count that does not grow with the page."""

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.contact_list import ContactFilters, filtered_contacts
from services.customers.models import Account, Call, Contact, Customer
from services.customers.tests.test_views import blind_to_one_account

URL = "/api/v1/contacts/"


class Fixture(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.kraft = Customer.objects.create(organisation=self.org, name="Kraft Heinz")
        self.emea = Account.objects.create(name="EMEA")
        self.emea.customers.add(self.pizza)
        self.sam = self.person("Sam Pizza", customer=self.pizza, role="decision_maker")
        self.uma = self.person("Uma Hut", account=self.emea, sentiment="negative", role="champion")
        self.kim = self.person(
            "Kim Kraft", customer=self.kraft, sentiment="positive", role="economic_buyer"
        )

    def person(self, name, sentiment="neutral", role="other", **parent):
        return Contact.objects.create(
            name=name,
            email=f"{name.split()[0].lower()}@example.com",
            sentiment=sentiment,
            role=role,
            **parent,
        )

    def names(self, query="", user=None):
        self.client.force_authenticate(user or self.admin)
        response = self.client.get(URL + query)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return sorted(row["name"] for row in response.data["results"])

    def joint(self):
        """A contact on an Account linked to two organisations: `mine`,
        which the viewer owns, and the colleague's `secret`, which the
        viewer cannot open. The viewer sees the contact and the Account
        through `mine`, but must never be shown `secret`."""
        viewer, _seen, _hidden = blind_to_one_account(self.pizza)
        colleague = self.pizza.owner
        mine = Customer.objects.create(organisation=self.org, name="Mine", owner=viewer)
        secret = Customer.objects.create(organisation=self.org, name="Secret Co", owner=colleague)
        joint_account = Account.objects.create(name="Joint", owner=colleague)
        joint_account.customers.add(mine, secret)
        contact = self.person("Jo Joint", account=joint_account)
        return viewer, mine, secret, joint_account, contact


class FilterTests(Fixture):
    def test_the_organisation_filter_takes_its_accounts_contacts_too(self):
        self.assertEqual(self.names(f"?customer={self.pizza.id}"), ["Sam Pizza", "Uma Hut"])
        # The older name still works for the page that sends it.
        self.assertEqual(self.names(f"?company={self.pizza.id}"), ["Sam Pizza", "Uma Hut"])

    def test_the_account_filter(self):
        self.assertEqual(self.names(f"?account={self.emea.id}"), ["Uma Hut"])

    def test_the_sentiment_and_role_filters(self):
        self.assertEqual(self.names("?sentiment=negative"), ["Uma Hut"])
        self.assertEqual(self.names("?role=economic_buyer"), ["Kim Kraft"])

    def test_filters_combine_with_search(self):
        self.assertEqual(self.names(f"?customer={self.pizza.id}&search=uma"), ["Uma Hut"])

    def test_unusable_values_are_ignored(self):
        everyone = ["Kim Kraft", "Sam Pizza", "Uma Hut"]
        self.assertEqual(self.names("?customer=x&account=&sentiment=furious&role=boss"), everyone)

    def test_an_organisation_the_viewer_cannot_open_matches_nothing(self):
        # Before the fix, filtering by `secret`'s id still matched Jo Joint
        # through the Joint account's other link to `mine` — confirming an
        # organisation exists that the viewer may not open.
        viewer, _mine, secret, _joint, _jo = self.joint()
        self.assertEqual(self.names(f"?customer={secret.id}", user=viewer), [])

    def test_an_account_the_viewer_cannot_open_matches_nothing(self):
        viewer, _seen, hidden = blind_to_one_account(self.pizza)
        self.person("Hal Hidden", account=hidden)
        self.assertEqual(self.names(f"?account={hidden.id}", user=viewer), [])

    def test_a_negative_or_zero_id_matches_nobody_rather_than_being_ignored(self):
        # -1/0 parse fine as ints, so they are real ids to check visibility
        # for, not a value to drop like "x" or "" — nobody has that id, so
        # these behave the same as any other id the caller cannot open.
        self.assertEqual(self.names("?customer=-1"), [])
        self.assertEqual(self.names("?account=0"), [])


class RowTests(Fixture):
    def row(self, name, query=""):
        self.client.force_authenticate(self.admin)
        rows = self.client.get(URL + query).data["results"]
        return next(row for row in rows if row["name"] == name)

    def test_an_account_contact_names_its_organisation_and_account(self):
        row = self.row("Uma Hut")
        self.assertEqual(row["organisation"], {"id": self.pizza.id, "name": "Pizza Hut"})
        self.assertEqual(row["account"], {"id": self.emea.id, "name": "EMEA"})

    def test_an_organisation_contact_has_no_account(self):
        row = self.row("Sam Pizza")
        self.assertEqual(row["organisation"], {"id": self.pizza.id, "name": "Pizza Hut"})
        self.assertIsNone(row["account"])

    def test_a_row_carries_its_sentiment_and_last_contact(self):
        row = self.row("Sam Pizza")
        for field in ("sentiment", "sentiment_source", "calls", "last_contacted_at"):
            self.assertIn(field, row)
        self.assertNotIn("sentiment_evidence", row)

    def test_an_organisation_the_viewer_cannot_open_is_not_named(self):
        # Joint belongs to two organisations: the viewer's own, and the
        # colleague's Secret Co, which the viewer cannot open. The viewer sees
        # Joint through their own organisation.
        viewer, mine, _secret, joint, _jo = self.joint()
        self.client.force_authenticate(viewer)
        row = next(r for r in self.client.get(URL).data["results"] if r["name"] == "Jo Joint")
        self.assertEqual(row["companies"], [{"id": mine.id, "name": "Mine"}])
        self.assertEqual(row["organisation"], {"id": mine.id, "name": "Mine"})
        self.assertEqual(row["account"], {"id": joint.id, "name": "Joint"})


class CallCountTests(Fixture):
    """`calls`: every call this person was on that the viewer may open —
    company rule only, calls have no record rule of their own."""

    def call(self, contact, sentiment="neutral", **parent):
        call = Call.objects.create(
            title="A call",
            host_name="Carl",
            occurred_at=timezone.now(),
            sentiment=sentiment,
            **parent,
        )
        call.participants.add(contact)
        return call

    def row_for(self, name, user):
        self.client.force_authenticate(user)
        rows = self.client.get(URL).data["results"]
        return next(r for r in rows if r["name"] == name)

    def test_a_hidden_account_call_is_excluded(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.call(self.sam, customer=self.pizza)
        self.call(self.sam, account=seen)
        self.call(self.sam, account=hidden)

        self.assertEqual(self.row_for("Sam Pizza", viewer)["calls"], 2)

        colleague = self.pizza.owner
        self.assertEqual(self.row_for("Sam Pizza", colleague)["calls"], 3)

    def test_a_pinned_query_count_holds_the_annotation(self):
        for _ in range(5):
            self.call(self.sam, customer=self.pizza)
        self.client.force_authenticate(self.admin)
        self.client.get(URL)  # memoise the caller's org chart, as elsewhere
        with self.assertNumQueries(5):
            row = self.row_for("Sam Pizza", self.admin)
        self.assertEqual(row["calls"], 5)


class NestedCallCountQueryTests(Fixture):
    """CustomerContactListView/AccountContactListView have no pagination,
    so `readable_calls_count` matters even more there — one annotated
    query for the whole page, flat as the page grows, same as the
    standalone list's own CallCountTests."""

    def call(self, contact, **parent):
        call = Call.objects.create(
            title="A call", host_name="Carl", occurred_at=timezone.now(), **parent
        )
        call.participants.add(contact)
        return call

    def test_the_customer_contact_list_query_count_holds_from_1_to_5_contacts(self):
        url = f"/api/v1/customers/{self.pizza.id}/contacts/"
        self.client.force_authenticate(self.admin)
        self.call(self.sam, customer=self.pizza)
        self.client.get(url)  # memoise the caller's org chart, as elsewhere
        with self.assertNumQueries(4):
            few = self.client.get(url).data
        for n in range(4):
            contact = self.person(f"Extra{n} Pizza", customer=self.pizza)
            self.call(contact, customer=self.pizza)
        with self.assertNumQueries(4):
            many = self.client.get(url).data
        self.assertEqual(len(few), 2)  # Sam (org-level) + Uma (via EMEA)
        self.assertEqual(len(many), 6)
        self.assertEqual(next(r for r in many if r["name"] == "Sam Pizza")["calls"], 1)

    def test_the_account_contact_list_query_count_holds_from_1_to_5_contacts(self):
        url = f"/api/v1/customers/{self.pizza.id}/accounts/{self.emea.id}/contacts/"
        self.client.force_authenticate(self.admin)
        self.call(self.uma, account=self.emea)
        self.client.get(url)
        with self.assertNumQueries(4):
            few = self.client.get(url).data
        for n in range(4):
            contact = self.person(f"Extra{n} Emea", account=self.emea)
            self.call(contact, account=self.emea)
        with self.assertNumQueries(4):
            many = self.client.get(url).data
        self.assertEqual(len(few), 1)
        self.assertEqual(len(many), 5)
        self.assertEqual(next(r for r in many if r["name"] == "Uma Hut")["calls"], 1)

    def test_no_row_is_duplicated_when_an_account_spans_two_organisations(self):
        # The same fan-out shape blind_to_one_account/joint() exercises
        # elsewhere: an Account (here EMEA) linked to two Customers must not
        # duplicate a Contact row once the Count annotation joins in.
        self.kraft_emea_link = self.emea.customers.add(self.kraft)
        self.call(self.uma, account=self.emea)
        self.call(self.uma, account=self.emea)
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/customers/{self.pizza.id}/accounts/{self.emea.id}/contacts/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        rows = [r for r in response.data if r["name"] == "Uma Hut"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["calls"], 2)


class NestedVisibilityTests(Fixture):
    """The same "only what the viewer may open" rule, carried onto the
    other three places a Contact's `companies`/`organisation` are read."""

    def test_the_detail_view_names_only_the_visible_organisation(self):
        viewer, mine, _secret, _joint, contact = self.joint()
        self.client.force_authenticate(viewer)
        response = self.client.get(f"/api/v1/contacts/{contact.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["companies"], [{"id": mine.id, "name": "Mine"}])
        self.assertEqual(response.data["organisation"], {"id": mine.id, "name": "Mine"})

    def test_the_customer_contact_list_names_only_the_visible_organisation(self):
        viewer, mine, _secret, _joint, _contact = self.joint()
        self.client.force_authenticate(viewer)
        response = self.client.get(f"/api/v1/customers/{mine.id}/contacts/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        row = next(r for r in response.data if r["name"] == "Jo Joint")
        self.assertEqual(row["companies"], [{"id": mine.id, "name": "Mine"}])
        self.assertEqual(row["organisation"], {"id": mine.id, "name": "Mine"})

    def test_the_account_contact_list_names_only_the_visible_organisation(self):
        viewer, mine, _secret, joint, _contact = self.joint()
        self.client.force_authenticate(viewer)
        response = self.client.get(f"/api/v1/customers/{mine.id}/accounts/{joint.id}/contacts/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        row = next(r for r in response.data if r["name"] == "Jo Joint")
        self.assertEqual(row["companies"], [{"id": mine.id, "name": "Mine"}])
        self.assertEqual(row["organisation"], {"id": mine.id, "name": "Mine"})


class SummaryTests(Fixture):
    def summary(self, query="", user=None):
        self.client.force_authenticate(user or self.admin)
        return self.client.get(URL + query).data["summary"]

    def test_the_summary_counts_the_whole_filtered_set(self):
        for n in range(30):
            self.person(f"Guest{n} Pizza", customer=self.pizza, sentiment="positive")
        self.assertEqual(
            self.summary(f"?customer={self.pizza.id}"),
            {
                "total": 32,
                "positive": 30,
                "neutral": 1,
                "negative": 1,
                "decision_makers": 1,
                "active": 32,
                "growth_30d_pct": None,
            },
        )
        self.assertEqual(self.summary()["decision_makers"], 2)

    def test_the_summary_follows_visibility(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.person("Sue Seen", account=seen, sentiment="positive")
        self.person("Hal Hidden", account=hidden, sentiment="negative")
        # The viewer sees Pizza Hut (through Seen), its own contacts and
        # Seen's, and the unowned EMEA and Kraft Heinz.
        self.assertEqual(self.names(user=viewer), ["Kim Kraft", "Sam Pizza", "Sue Seen", "Uma Hut"])
        self.assertEqual(self.summary(user=viewer)["total"], 4)


class ActiveAndGrowthTests(Fixture):
    """The old stat cards' Active contacts and Growth (30d), kept on the
    summary with ContactStatsView's own definitions, over the filtered set."""

    def setUp(self):
        super().setUp()
        from datetime import timedelta

        from django.utils import timezone

        old = timezone.now() - timedelta(days=40)
        Contact.objects.filter(pk__in=[self.sam.pk, self.kim.pk]).update(created_at=old)
        Contact.objects.filter(pk=self.uma.pk).update(status=Contact.Status.INACTIVE)

    def summary(self, query="", user=None):
        self.client.force_authenticate(user or self.admin)
        return self.client.get(URL + query).data["summary"]

    def test_unfiltered(self):
        summary = self.summary()
        self.assertEqual((summary["active"], summary["growth_30d_pct"]), (2, 50.0))

    def test_filtered(self):
        summary = self.summary(f"?customer={self.pizza.id}")
        self.assertEqual((summary["total"], summary["active"]), (2, 1))
        self.assertEqual(summary["growth_30d_pct"], 100.0)
        summary = self.summary(f"?account={self.emea.id}")
        self.assertEqual((summary["active"], summary["growth_30d_pct"]), (0, None))

    def test_the_same_as_the_stats_view_unfiltered(self):
        self.client.force_authenticate(self.admin)
        stats = self.client.get(f"{URL}stats/").data
        summary = self.summary()
        self.assertEqual(
            (summary["active"], summary["growth_30d_pct"]),
            (stats["active"], stats["growth_30d_pct"]),
        )

    def test_an_invisible_filter_gives_an_empty_summary(self):
        viewer, _mine, secret, _joint, _jo = self.joint()
        self.assertEqual(
            self.summary(f"?customer={secret.id}", user=viewer),
            {
                "total": 0,
                "positive": 0,
                "neutral": 0,
                "negative": 0,
                "decision_makers": 0,
                "active": 0,
                "growth_30d_pct": None,
            },
        )


class QueryCountTests(Fixture):
    def test_the_query_count_does_not_grow_with_the_page(self):
        self.client.force_authenticate(self.admin)
        # The first read resolves the caller's membership and org chart, which
        # are memoised on the user; what is pinned is every read after it.
        self.client.get(URL)
        for batch in range(2):
            for n in range(6):
                self.person(f"Org{batch}{n} Person", customer=self.pizza)
                self.person(f"Acc{batch}{n} Person", account=self.emea)
            with self.assertNumQueries(5):
                response = self.client.get(URL)
            self.assertEqual(response.data["count"], 3 + 12 * (batch + 1))


class OrderingTests(Fixture):
    """The `readable_calls_count` annotation turns this queryset into a
    GROUP BY query, which Django does not carry `Meta.ordering` onto — an
    explicit `.order_by("name", "pk")` is what keeps a page from repeating
    or skipping a row (UnorderedObjectListWarning)."""

    def test_the_list_stays_in_name_order_across_pages_and_never_warns(self):
        import warnings

        self.client.force_authenticate(self.admin)
        for i in range(30):
            self.person(f"Extra{i:02d} Pizza", customer=self.pizza)
        # Ground truth: the same production queryset (annotation, order_by
        # and all), not a Python-side re-sort that could disagree with the
        # database's own collation.
        expected = list(
            filtered_contacts(self.admin, ContactFilters()).values_list("id", flat=True)
        )
        self.assertEqual(len(expected), 33)  # Sam, Uma, Kim + the 30 Extras

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            first = self.client.get(URL)
            second = self.client.get(URL, {"page": 2})

        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        seen = [r["id"] for r in first.data["results"]] + [r["id"] for r in second.data["results"]]
        self.assertEqual(seen, expected)  # no repeat, no skip, in name order
        self.assertEqual(len(set(seen)), len(expected))  # belt-and-braces: no duplicate row
