"""Company-wide knowledge: anyone in the organisation reads and writes it,
the author's function is stamped, responsibility is a lookup."""

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Customer
from services.knowledge.models import Contribution, FunctionOwner


class KnowledgeViewTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.alice = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
            function=User.Function.LEADERSHIP,
        )
        self.carl = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.priya = User.objects.create_user(
            email="priya@acme.io",
            password="x",
            name="Priya",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.ENGINEERING,
        )
        self.dana = User.objects.create_user(
            email="dana@acme.io",
            password="x",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        other = Organisation.objects.create(name="Globex")
        self.outsider = User.objects.create_user(
            email="zed@globex.io", password="x", name="Zed", organisation=other
        )
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.carl
        )
        # The org chart: everyone reports to Alice, so leadership sees all
        # (services.accounts.hierarchy); Dana and Priya are on different
        # branches and teams.
        User.objects.filter(pk__in=[self.carl.pk, self.priya.pk, self.dana.pk]).update(
            reports_to=self.alice
        )
        # The in-memory users must see their new manager too.
        for person in User.objects.filter(organisation=self.org):
            for attr in vars(self):
                if (
                    getattr(self, attr, None).__class__ is User
                    and getattr(self, attr).pk == person.pk
                ):
                    getattr(self, attr).refresh_from_db()

    def test_anyone_in_the_organisation_contributes_and_reads_whatever_their_book(self):
        # Priya owns nothing and is not a CSM; Dana is a CSM who does not own Pizza Hut.
        self.client.force_authenticate(self.priya)
        written = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/contributions/",
            {"body": "SSO drops sessions on token refresh; fix in 2.4."},
            format="json",
        )
        self.assertEqual(written.status_code, status.HTTP_201_CREATED)
        self.assertEqual(written.data["function"], "engineering")
        self.assertEqual(written.data["author"]["name"], "Priya")

        # Alice owns no book, but leadership at the top of the chart sees all.
        self.client.force_authenticate(self.alice)
        listed = self.client.get(f"/api/v1/customers/{self.pizza.id}/contributions/")
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual([c["function_display"] for c in listed.data], ["Engineering"])
        # Dana is CS on another branch: Priya's engineering note is not hers to read.
        self.client.force_authenticate(self.dana)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{self.pizza.id}/contributions/").data, []
        )

        self.client.force_authenticate(self.outsider)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{self.pizza.id}/contributions/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_only_the_author_or_a_user_manager_changes_a_contribution(self):
        row = Contribution.objects.create(
            organisation=self.org,
            customer=self.pizza,
            author=self.priya,
            function="engineering",
            body="first",
        )
        self.client.force_authenticate(self.dana)
        self.assertEqual(
            self.client.patch(
                f"/api/v1/contributions/{row.id}/", {"body": "x"}, format="json"
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.client.force_authenticate(self.priya)
        self.assertEqual(
            self.client.patch(
                f"/api/v1/contributions/{row.id}/", {"body": "second"}, format="json"
            ).data["body"],
            "second",
        )
        self.client.force_authenticate(self.alice)
        self.assertEqual(
            self.client.delete(f"/api/v1/contributions/{row.id}/").status_code,
            status.HTTP_204_NO_CONTENT,
        )

    def test_responsible_reads_cs_from_the_owner_and_the_rest_from_function_owners(self):
        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)
        self.client.force_authenticate(self.dana)
        by = {
            r["function"]: r["user"]
            for r in self.client.get(f"/api/v1/customers/{self.pizza.id}/responsible/").data[
                "responsible"
            ]
        }
        self.assertEqual(by["cs"]["name"], "Carl")
        self.assertEqual(by["engineering"]["name"], "Priya")
        self.assertIsNone(by["sales"])

        # Setting needs view-all-accounts.
        self.assertEqual(
            self.client.patch(
                f"/api/v1/customers/{self.pizza.id}/responsible/",
                {"function": "sales", "user_id": self.dana.id},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.client.force_authenticate(self.alice)
        # Dana is a CSM: she cannot be the sales owner. Someone in Sales can.
        refused = self.client.patch(
            f"/api/v1/customers/{self.pizza.id}/responsible/",
            {"function": "sales", "user_id": self.dana.id},
            format="json",
        )
        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            refused.data["detail"],
            "Dana is in Customer Success, not Sales. Pick someone from Sales.",
        )
        self.assertFalse(self.pizza.function_owners.filter(function="sales").exists())
        raj = User.objects.create_user(
            email="raj@acme.io",
            password="x",
            name="Raj",
            organisation=self.org,
            function=User.Function.SALES,
        )
        set_ = self.client.patch(
            f"/api/v1/customers/{self.pizza.id}/responsible/",
            {"function": "sales", "user_id": raj.id},
            format="json",
        )
        by = {r["function"]: r["user"] for r in set_.data["responsible"]}
        self.assertEqual(by["sales"]["name"], "Raj")
        # The model refuses it too, whoever writes the row.
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            FunctionOwner.objects.create(customer=self.pizza, function="sales", user=self.dana)
        cleared = self.client.patch(
            f"/api/v1/customers/{self.pizza.id}/responsible/",
            {"function": "engineering", "user_id": None},
            format="json",
        )
        self.assertIsNone(
            {r["function"]: r["user"] for r in cleared.data["responsible"]}["engineering"]
        )
        self.assertEqual(
            self.client.patch(
                f"/api/v1/customers/{self.pizza.id}/responsible/",
                {"function": "magic", "user_id": self.dana.id},
                format="json",
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )


class CopilotKnowledgeTests(APITestCase):
    """The Copilot reads every function's knowledge and names who answers."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.ceo = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
            function=User.Function.LEADERSHIP,
        )
        self.carl = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.priya = User.objects.create_user(
            email="priya@acme.io",
            password="x",
            name="Priya",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.ENGINEERING,
        )
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.carl
        )
        User.objects.filter(pk__in=[self.carl.pk, self.priya.pk]).update(reports_to=self.ceo)
        # The in-memory users must see their new manager too.
        for person in User.objects.filter(organisation=self.org):
            for attr in vars(self):
                if (
                    getattr(self, attr, None).__class__ is User
                    and getattr(self, attr).pk == person.pk
                ):
                    getattr(self, attr).refresh_from_db()
        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)
        Contribution.objects.create(
            organisation=self.org,
            customer=self.pizza,
            author=self.priya,
            function="engineering",
            body="SSO drops sessions on token refresh; fix in 2.4.",
        )

    def test_a_leader_with_no_book_gets_the_companys_knowledge_and_the_responsible_people(self):
        from services.copilot.context import build_grounding

        grounding = build_grounding(self.org, self.ceo, query="What is going on with Pizza Hut?")

        self.assertIn("answering from what the company knows", grounding.summary)
        self.assertIn("Engineering (Priya,", grounding.summary)
        self.assertIn("SSO drops sessions", grounding.summary)
        self.assertIn(
            "Responsible for Pizza Hut: Engineering — Priya; Customer Success — Carl",
            grounding.summary,
        )
        self.assertEqual(grounding.sources[0]["type"], "contribution")
        self.assertEqual(grounding.sources[0]["label"], "Engineering · Priya")
