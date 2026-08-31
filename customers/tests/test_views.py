"""Integration tier: through the real URLconf + real test DB."""

from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Organisation, User
from customers.models import Customer


class CustomerListCreateTests(APITestCase):
    url = "/api/v1/customers/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_can_list_and_create(self):
        Customer.objects.create(organisation=self.org, name="Globex")
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

        response = self.client.post(
            self.url,
            {"name": "Initech", "health_score": 35, "lifecycle_stage": "churn"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["health_category"], "poor")
        self.assertEqual(Customer.objects.get(name="Initech").organisation_id, self.org.id)

    def test_csm_can_also_list_and_create(self):
        """Unlike User Management, customer records aren't admin-gated."""
        self.client.force_authenticate(self.csm)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.post(self.url, {"name": "Initech"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_does_not_leak_another_organisations_customers(self):
        other_org = Organisation.objects.create(name="Other Org")
        Customer.objects.create(organisation=other_org, name="Not Yours")

        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.data["count"], 0)

    def test_created_customer_is_assigned_to_the_callers_organisation_not_a_supplied_one(self):
        other_org = Organisation.objects.create(name="Other Org")
        self.client.force_authenticate(self.admin)

        # Even if a client tries to sneak in a different organisation id,
        # the view always uses the caller's own.
        response = self.client.post(
            self.url, {"name": "Sneaky Co", "organisation": other_org.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Customer.objects.get(name="Sneaky Co").organisation_id, self.org.id)


class CustomerDetailTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.url = f"/api/v1/customers/{self.customer.id}/"

    def test_admin_can_assign_a_same_org_owner(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(self.url, {"owner_id": self.csm.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["owner"]["email"], "carl@acme.io")

    def test_cannot_assign_an_owner_from_another_organisation(self):
        other_org = Organisation.objects.create(name="Other Org")
        outsider = User.objects.create_user(
            email="outsider@other.io",
            password="supersecret1",
            name="Outsider",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(self.url, {"owner_id": outsider.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("owner_id", response.data)
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.owner)

    def test_another_organisations_admin_gets_404_not_403(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(other_admin)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        response = self.client.patch(self.url, {"name": "Pwned"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.name, "Globex")
