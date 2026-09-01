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
            {"name": "Initech", "health_score": "3.5", "lifecycle_stage": "churn"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["health_category"], "poor")
        self.assertEqual(Customer.objects.get(name="Initech").organisation_id, self.org.id)

    def test_create_sets_created_by_and_modified_by_from_the_caller(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.url, {"name": "Initech"}, format="json")
        self.assertEqual(response.data["created_by"]["email"], "alice@acme.io")
        self.assertEqual(response.data["modified_by"]["email"], "alice@acme.io")

    def test_create_accepts_the_full_field_set(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url,
            {
                "name": "Globex Corp",
                "address": "Cupertino, CA",
                "domain": "globex.com",
                "ai_pulse_score": "very_satisfied",
                "ai_pulse_reason": "Consistent high feature adoption.",
                "pulse": [1, 1, 1, 1, 1],
                "nps_score": 80,
                "csat_score": "97.50",
                "joined_date": "2024-10-19",
                "renewal_date": "2026-03-02",
                "contract_start_date": "2024-10-26",
                "contract_end_date": "2025-08-12",
                "arr_billed_at_account": "51200.00",
                "arr_billed_at_hq": "128300.00",
                "implementation_fee": "70000.00",
                "total_contract_value": "179500.00",
                "total_forecasted_renewal_revenue": "188475.00",
                "primary_product": "Product A",
                "additional_products_count": 3,
                "top_source_channel": "Talent Pool Re-engage",
                "total_contracted_seats": 560,
                "total_active_seats": 471,
                "total_hires": 124,
                "scope_web_app": "N/A",
                "ces_percentage": "98.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["seat_utilization_percentage"], 84.11)
        customer = Customer.objects.get(name="Globex Corp")
        self.assertEqual(customer.domain, "globex.com")
        self.assertEqual(str(customer.total_contract_value), "179500.00")

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


class CustomerSearchTests(APITestCase):
    """?search= on GET /api/v1/customers/ — matches name or Revenact ID
    (the row's own `id`), per the frontend's search box."""

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
        self.client.force_authenticate(self.admin)
        self.globex = Customer.objects.create(organisation=self.org, name="Globex Corp")
        self.initech = Customer.objects.create(organisation=self.org, name="Initech")

    def test_search_matches_name_case_insensitively(self):
        response = self.client.get(self.url, {"search": "globex"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "Globex Corp")

    def test_search_matches_a_substring_of_the_name(self):
        response = self.client.get(self.url, {"search": "tech"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "Initech")

    def test_search_matches_revenact_id(self):
        response = self.client.get(self.url, {"search": str(self.initech.id)})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], self.initech.id)

    def test_search_with_no_matches_returns_an_empty_page_not_an_error(self):
        response = self.client.get(self.url, {"search": "nonexistent-co"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)

    def test_blank_search_returns_everything(self):
        response = self.client.get(self.url, {"search": "  "})
        self.assertEqual(response.data["count"], 2)

    def test_search_still_scoped_to_the_callers_organisation(self):
        other_org = Organisation.objects.create(name="Other Org")
        Customer.objects.create(organisation=other_org, name="Globex Impostor")

        response = self.client.get(self.url, {"search": "globex"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], self.globex.id)


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

    def test_update_sets_modified_by_from_the_caller(self):
        self.client.force_authenticate(self.csm)
        response = self.client.patch(self.url, {"name": "Globex Renamed"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["modified_by"]["email"], "carl@acme.io")

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
