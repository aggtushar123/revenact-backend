"""GET /api/v1/contacts/ — the Contacts page's list: its filters, the
organisation and account on every row, a summary over the whole filtered
set, and a query count that does not grow with the page."""

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Account, Contact, Customer
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

    def test_a_row_carries_its_sentiment_evidence_and_last_contact(self):
        row = self.row("Sam Pizza")
        for field in ("sentiment", "sentiment_source", "sentiment_evidence", "last_contacted_at"):
            self.assertIn(field, row)

    def test_an_organisation_the_viewer_cannot_open_is_not_named(self):
        # Joint belongs to two organisations: the viewer's own, and the
        # colleague's Secret Co, which the viewer cannot open. The viewer sees
        # Joint through their own organisation.
        viewer, _seen, _hidden = blind_to_one_account(self.pizza)
        colleague = self.pizza.owner
        mine = Customer.objects.create(organisation=self.org, name="Mine", owner=viewer)
        secret = Customer.objects.create(organisation=self.org, name="Secret Co", owner=colleague)
        joint = Account.objects.create(name="Joint", owner=colleague)
        joint.customers.add(mine, secret)
        self.person("Jo Joint", account=joint)
        self.client.force_authenticate(viewer)
        row = next(r for r in self.client.get(URL).data["results"] if r["name"] == "Jo Joint")
        self.assertEqual(row["companies"], [{"id": mine.id, "name": "Mine"}])
        self.assertEqual(row["organisation"], {"id": mine.id, "name": "Mine"})
        self.assertEqual(row["account"], {"id": joint.id, "name": "Joint"})


class SummaryTests(Fixture):
    def summary(self, query="", user=None):
        self.client.force_authenticate(user or self.admin)
        return self.client.get(URL + query).data["summary"]

    def test_the_summary_counts_the_whole_filtered_set(self):
        for n in range(30):
            self.person(f"Guest{n} Pizza", customer=self.pizza, sentiment="positive")
        self.assertEqual(
            self.summary(f"?customer={self.pizza.id}"),
            {"total": 32, "positive": 30, "neutral": 1, "negative": 1, "decision_makers": 1},
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
