"""Opportunities and risks are read department-wise, and by role."""

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, Role, User
from services.customers.models import Account, Customer, Opportunity, Risk


class Fixture(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
            function=User.Function.CS,
        )
        self.sales = User.objects.create_user(
            email="sid@acme.io",
            password="x",
            name="Sid",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.SALES,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.leader = User.objects.create_user(
            email="lee@acme.io",
            password="x",
            name="Lee",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.LEADERSHIP,
        )
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.hut_uk = Account.objects.create(name="Pizza Hut UK")
        self.hut_uk.customers.add(self.pizza)
        self.sales_opp = Opportunity.objects.create(
            customer=self.pizza, title="Upsell", mrr=100, department="sales"
        )
        self.cs_opp = Opportunity.objects.create(
            account=self.hut_uk, title="Renewal", mrr=200, department="cs"
        )
        self.shared_opp = Opportunity.objects.create(customer=self.pizza, title="Shared", mrr=300)
        self.cs_risk = Risk.objects.create(
            customer=self.pizza, title="Churn", mrr=50, department="cs"
        )
        self.eng_risk = Risk.objects.create(
            customer=self.pizza, title="Outage", mrr=60, department="engineering"
        )

    def titles(self, user, path):
        self.client.force_authenticate(user)
        response = self.client.get(path)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return sorted(row["title"] for row in response.data)


class VisibilityTests(Fixture):
    def test_a_person_sees_their_departments_items_plus_the_shared_ones(self):
        self.assertEqual(self.titles(self.sales, "/api/v1/opportunities/"), ["Shared", "Upsell"])
        self.assertEqual(self.titles(self.csm, "/api/v1/opportunities/"), ["Renewal", "Shared"])
        self.assertEqual(self.titles(self.csm, "/api/v1/risks/"), ["Churn"])
        self.assertEqual(self.titles(self.sales, "/api/v1/risks/"), [])

    def test_a_role_that_views_all_accounts_and_leadership_see_every_department(self):
        everything = ["Renewal", "Shared", "Upsell"]
        self.assertEqual(self.titles(self.admin, "/api/v1/opportunities/"), everything)
        self.assertEqual(self.titles(self.leader, "/api/v1/opportunities/"), everything)
        self.assertEqual(self.titles(self.leader, "/api/v1/risks/"), ["Churn", "Outage"])

    def test_the_nested_lists_on_a_company_follow_the_same_rule(self):
        self.assertEqual(
            self.titles(self.sales, f"/api/v1/customers/{self.pizza.id}/opportunities/"),
            ["Shared", "Upsell"],
        )
        self.assertEqual(
            self.titles(
                self.csm,
                f"/api/v1/customers/{self.pizza.id}/accounts/{self.hut_uk.id}/opportunities/",
            ),
            ["Renewal"],
        )
        self.assertEqual(
            self.titles(
                self.sales,
                f"/api/v1/customers/{self.pizza.id}/accounts/{self.hut_uk.id}/opportunities/",
            ),
            [],
        )
        self.assertEqual(
            self.titles(self.csm, f"/api/v1/customers/{self.pizza.id}/risks/"), ["Churn"]
        )

    def test_another_departments_item_is_a_404_by_id(self):
        self.client.force_authenticate(self.sales)
        self.assertEqual(
            self.client.get(f"/api/v1/opportunities/{self.cs_opp.id}/").status_code, 404
        )
        self.assertEqual(
            self.client.patch(
                f"/api/v1/opportunities/{self.cs_opp.id}/", {"stage": "negotiation"}, format="json"
            ).status_code,
            404,
        )
        self.assertEqual(self.client.get(f"/api/v1/risks/{self.eng_risk.id}/").status_code, 404)
        self.assertEqual(
            self.client.get(f"/api/v1/opportunities/{self.sales_opp.id}/").status_code, 200
        )

    def test_rows_carry_the_department(self):
        self.client.force_authenticate(self.leader)
        row = next(
            r for r in self.client.get("/api/v1/opportunities/").data if r["title"] == "Upsell"
        )
        self.assertEqual((row["department"], row["department_display"]), ("sales", "Sales"))
        shared = next(
            r for r in self.client.get("/api/v1/opportunities/").data if r["title"] == "Shared"
        )
        self.assertEqual((shared["department"], shared["department_display"]), ("", ""))


class CreateTests(Fixture):
    def test_a_new_item_lands_on_its_creators_department_unless_told_otherwise(self):
        self.client.force_authenticate(self.sales)
        response = self.client.post(
            "/api/v1/opportunities/",
            {"title": "New deal", "mrr": "10", "customer_id": self.pizza.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["department"], "sales")

        response = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/risks/",
            {"title": "Shared risk", "mrr": "1", "department": ""},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["department"], "")

        response = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/accounts/{self.hut_uk.id}/opportunities/",
            {"title": "Eng ask", "mrr": "1", "department": "engineering"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["department"], "engineering")

    def test_an_unknown_department_is_refused(self):
        self.client.force_authenticate(self.sales)
        response = self.client.post(
            "/api/v1/opportunities/",
            {"title": "x", "mrr": "1", "customer_id": self.pizza.id, "department": "marketing"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_department_can_be_changed_on_an_item_you_may_see(self):
        self.client.force_authenticate(self.leader)
        response = self.client.patch(
            f"/api/v1/opportunities/{self.sales_opp.id}/", {"department": "cs"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["department_display"], "Customer Success")


__all__ = ["Role"]
