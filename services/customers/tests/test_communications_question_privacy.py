"""The team queue on Communications names a report's question's customer only
to a manager who may open it.

A question routed to Dana opens its customer to her (`visible_customers`,
`questions__assignee`), not to Carl, her manager, who reads her open
questions under `?scope=team`. For a customer he may not open, the row comes
without `account` and `context` (its health, ARR, renewal and owner), and a
search by that name does not find it."""

from services.customers.models import Customer
from services.customers.scoping import visible_customers
from services.knowledge.models import Question
from services.knowledge.tests.test_hierarchy import ChartFixture

URL = "/api/v1/communications/"


class TeamQueueQuestionTests(ChartFixture):
    def setUp(self):
        super().setUp()
        # Raj's customer: Carl has no way to open it.
        self.secret = Customer.objects.create(
            organisation=self.org, name="Secret Corp", owner=self.raj, health_score="3.1"
        )

    def ask_dana(self, customer):
        return Question.objects.create(
            organisation=self.org,
            customer=customer,
            asked_by=self.raj,
            assignee=self.dana,
            text="Can you check their renewal?",
        )

    def team_rows(self, user, **query):
        self.client.force_authenticate(user)
        response = self.client.get(URL, {"scope": "team", "kind": "question", **query})
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def test_a_manager_who_cannot_open_the_customer_gets_it_unnamed(self):
        question = self.ask_dana(self.secret)
        self.assertFalse(visible_customers(self.carl).filter(pk=self.secret.pk).exists())

        response = self.team_rows(self.carl)

        (row,) = response.data["results"]
        self.assertEqual(row["id"], f"question:{question.pk}")
        self.assertIsNone(row["account"])
        self.assertIsNone(row["context"])
        self.assertNotIn("Secret Corp", response.content.decode())
        self.assertEqual(self.team_rows(self.carl, q="secret").data["results"], [])

    def test_a_manager_who_can_open_the_customer_gets_it_named(self):
        self.ask_dana(self.pizza)

        (row,) = self.team_rows(self.carl).data["results"]

        self.assertEqual(
            row["account"], {"id": self.pizza.pk, "name": "Pizza Hut", "type": "customer"}
        )
        self.assertIsNotNone(row["context"])

    def test_the_person_asked_still_gets_it_named(self):
        self.ask_dana(self.secret)
        self.client.force_authenticate(self.dana)

        (row,) = self.client.get(URL, {"kind": "question"}).data["results"]

        self.assertEqual(row["account"]["name"], "Secret Corp")
        self.assertIsNotNone(row["context"])
