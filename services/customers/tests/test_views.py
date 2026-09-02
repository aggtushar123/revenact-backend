"""Integration tier: through the real URLconf + real test DB."""

from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Account, Activity, Customer


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


class CustomerRenewalWindowTests(APITestCase):
    """?renewal_within= on GET /api/v1/customers/ — powers the
    Organizations page's Renewal card/popover."""

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
        self.today = timezone.localdate()

        self.soon = Customer.objects.create(
            organisation=self.org, name="Renews Soon", renewal_date=self.today + timedelta(days=10)
        )
        self.later = Customer.objects.create(
            organisation=self.org, name="Renews Later", renewal_date=self.today + timedelta(days=45)
        )
        self.far = Customer.objects.create(
            organisation=self.org,
            name="Renews Far Out",
            renewal_date=self.today + timedelta(days=100),
        )
        Customer.objects.create(organisation=self.org, name="No Renewal Date Set")
        Customer.objects.create(
            organisation=self.org,
            name="Already Renewed",
            renewal_date=self.today - timedelta(days=5),
        )
        Customer.objects.create(
            organisation=self.org,
            name="Churned But Due Soon",
            renewal_date=self.today + timedelta(days=5),
            lifecycle_stage=Customer.LifecycleStage.CHURN,
        )

    def test_within_30_days_includes_the_overdue_one_and_the_soon_one_ordered_by_date(self):
        response = self.client.get(self.url, {"renewal_within": 30})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [c["name"] for c in response.data["results"]]
        # "Already Renewed" is overdue (renewal_date in the past) — that's
        # *more* urgent than "Renews Soon", not excluded, so it sorts first.
        self.assertEqual(names, ["Already Renewed", "Renews Soon"])

    def test_within_90_days_also_includes_later_but_not_far_out(self):
        response = self.client.get(self.url, {"renewal_within": 90})
        names = [c["name"] for c in response.data["results"]]
        self.assertEqual(names, ["Already Renewed", "Renews Soon", "Renews Later"])

    def test_excludes_customers_with_no_renewal_date(self):
        response = self.client.get(self.url, {"renewal_within": 365})
        names = {c["name"] for c in response.data["results"]}
        self.assertNotIn("No Renewal Date Set", names)

    def test_includes_a_renewal_date_already_in_the_past_as_overdue(self):
        # An overdue renewal needs attention *more* urgently than an
        # upcoming one, not less — there's no lower bound on the window.
        response = self.client.get(self.url, {"renewal_within": 365})
        names = {c["name"] for c in response.data["results"]}
        self.assertIn("Already Renewed", names)

    def test_excludes_churned_customers_even_if_their_renewal_date_is_due(self):
        response = self.client.get(self.url, {"renewal_within": 30})
        names = {c["name"] for c in response.data["results"]}
        self.assertNotIn("Churned But Due Soon", names)

    def test_non_integer_value_is_ignored_rather_than_erroring(self):
        response = self.client.get(self.url, {"renewal_within": "soon-ish"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Falls through to the unfiltered (name-ordered) listing.
        self.assertEqual(response.data["count"], 6)

    def test_omitted_param_returns_the_normal_unfiltered_listing(self):
        response = self.client.get(self.url)
        self.assertEqual(response.data["count"], 6)

    def test_still_scoped_to_the_callers_organisation(self):
        other_org = Organisation.objects.create(name="Other Org")
        Customer.objects.create(
            organisation=other_org, name="Not Yours", renewal_date=self.today + timedelta(days=1)
        )

        response = self.client.get(self.url, {"renewal_within": 30})
        names = {c["name"] for c in response.data["results"]}
        self.assertNotIn("Not Yours", names)


class CustomerStatsTests(APITestCase):
    """GET /api/v1/customers/stats/ — aggregate rollups for the
    Organizations page's MetricsPanel (Health/NPS/Lifecycle Stages)."""

    url = "/api/v1/customers/stats/"

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

    def test_unauthenticated_cannot_access(self):
        self.client.force_authenticate(None)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_empty_organisation_returns_zeroed_buckets_not_an_error(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["health"]["good"], {"count": 0, "mrr": 0, "arr": 0})
        self.assertEqual(
            response.data["nps"], {"promoters": 0, "passives": 0, "detractors": 0, "score": 0}
        )
        self.assertEqual(response.data["lifecycle"]["churn"], {"count": 0, "mrr": 0, "arr": 0})

    def test_buckets_by_health_category_and_sums_derived_mrr_and_arr(self):
        Customer.objects.create(
            organisation=self.org,
            name="Good Co",
            health_score="9.0",
            arr_billed_at_account="12000.00",
        )
        Customer.objects.create(
            organisation=self.org,
            name="Also Good Co",
            health_score="7.0",
            arr_billed_at_account="6000.00",
        )
        Customer.objects.create(
            organisation=self.org,
            name="Average Co",
            health_score="5.0",
            arr_billed_at_account="2400.00",
        )
        Customer.objects.create(
            organisation=self.org,
            name="Poor Co",
            health_score="1.0",
            arr_billed_at_account="1200.00",
        )

        response = self.client.get(self.url)

        good = response.data["health"]["good"]
        self.assertEqual(good["count"], 2)
        self.assertEqual(good["arr"], 18000.0)
        self.assertEqual(good["mrr"], 1500.0)  # 18000 / 12, derived -- no stored MRR field

        average = response.data["health"]["average"]
        self.assertEqual(average["count"], 1)
        self.assertEqual(average["arr"], 2400.0)
        self.assertEqual(average["mrr"], 200.0)

        poor = response.data["health"]["poor"]
        self.assertEqual(poor["count"], 1)
        self.assertEqual(poor["arr"], 1200.0)
        self.assertEqual(poor["mrr"], 100.0)

    def test_buckets_by_lifecycle_stage_counting_churned_customers_too(self):
        Customer.objects.create(
            organisation=self.org,
            name="Live Co",
            lifecycle_stage="live",
            arr_billed_at_account="12000.00",
        )
        Customer.objects.create(
            organisation=self.org,
            name="Churned Co",
            lifecycle_stage="churn",
            arr_billed_at_account="6000.00",
        )

        response = self.client.get(self.url)

        self.assertEqual(response.data["lifecycle"]["live"]["count"], 1)
        # Unlike ?renewal_within= on the list endpoint (which excludes
        # churned customers), this endpoint counts them -- "churn" is
        # itself one of the buckets, not something to leave out.
        self.assertEqual(response.data["lifecycle"]["churn"]["count"], 1)
        self.assertEqual(response.data["lifecycle"]["churn"]["arr"], 6000.0)

    def test_nps_breakdown_excludes_unscored_customers_from_counts_and_denominator(self):
        Customer.objects.create(organisation=self.org, name="Promoter 1", nps_score=80)
        Customer.objects.create(organisation=self.org, name="Promoter 2", nps_score=40)
        Customer.objects.create(organisation=self.org, name="Passive", nps_score=0)
        Customer.objects.create(organisation=self.org, name="Detractor", nps_score=-60)
        Customer.objects.create(organisation=self.org, name="Not Yet Scored", nps_score=None)

        response = self.client.get(self.url)
        nps = response.data["nps"]
        self.assertEqual(nps["promoters"], 2)
        self.assertEqual(nps["passives"], 1)
        self.assertEqual(nps["detractors"], 1)
        # round((2 - 1) / 4 * 100) = 25 -- the unscored customer doesn't
        # count towards the denominator either.
        self.assertEqual(nps["score"], 25)

    def test_scoped_to_the_callers_organisation(self):
        other_org = Organisation.objects.create(name="Other Org")
        Customer.objects.create(
            organisation=other_org,
            name="Not Yours",
            health_score="9.0",
            arr_billed_at_account="99999.00",
        )
        Customer.objects.create(
            organisation=self.org, name="Mine", health_score="9.0", arr_billed_at_account="100.00"
        )

        response = self.client.get(self.url)
        good = response.data["health"]["good"]
        self.assertEqual(good["count"], 1)
        self.assertEqual(good["arr"], 100.0)


class CustomerArchiveTests(APITestCase):
    """is_archived — soft-hides a customer from the list and stats
    endpoints (PATCH-able like any other field; no dedicated endpoint)."""

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
        self.customer = Customer.objects.create(
            organisation=self.org,
            name="Globex Corp",
            health_score="9.0",
            arr_billed_at_account="1200.00",
        )

    def test_new_customers_are_not_archived_by_default(self):
        response = self.client.get(f"{self.url}{self.customer.id}/")
        self.assertEqual(response.data["is_archived"], False)

    def test_archiving_hides_it_from_the_list(self):
        self.client.patch(f"{self.url}{self.customer.id}/", {"is_archived": True}, format="json")

        response = self.client.get(self.url)
        self.assertEqual(response.data["count"], 0)

    def test_archiving_hides_it_from_stats(self):
        self.client.patch(f"{self.url}{self.customer.id}/", {"is_archived": True}, format="json")

        response = self.client.get(f"{self.url}stats/")
        self.assertEqual(response.data["health"]["good"]["count"], 0)

    def test_archiving_hides_it_from_the_renewal_window(self):
        self.customer.renewal_date = timezone.localdate()
        self.customer.save()

        self.client.patch(f"{self.url}{self.customer.id}/", {"is_archived": True}, format="json")

        response = self.client.get(self.url, {"renewal_within": 30})
        self.assertEqual(response.data["count"], 0)

    def test_archived_customer_still_reachable_directly_and_can_be_unarchived(self):
        detail_url = f"{self.url}{self.customer.id}/"
        self.client.patch(detail_url, {"is_archived": True}, format="json")

        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["is_archived"], True)

        response = self.client.patch(detail_url, {"is_archived": False}, format="json")
        self.assertEqual(response.data["is_archived"], False)
        self.assertEqual(self.client.get(self.url).data["count"], 1)


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


class AccountListCreateTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/"

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_customers_accounts(self):
        Account.objects.create(customer=self.customer, name="North America", health_score=8.5)
        Account.objects.create(customer=self.customer, name="EMEA", health_score=6.0)
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = {row["name"] for row in response.data}
        self.assertEqual(names, {"North America", "EMEA"})

    def test_response_is_a_plain_list_not_paginated(self):
        Account.objects.create(customer=self.customer, name="North America")
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)
        self.assertIsInstance(response.data, list)

    def test_includes_derived_health_category_and_nested_owner(self):
        Account.objects.create(
            customer=self.customer, name="North America", health_score=8.5, owner=self.admin
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        row = response.data[0]
        self.assertEqual(row["health_category"], "good")
        self.assertEqual(row["owner"]["name"], "Alice")

    def test_empty_customer_returns_an_empty_list_not_an_error(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_does_not_leak_another_customers_accounts(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        Account.objects.create(customer=other_customer, name="Initech HQ")
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/accounts/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_another_organisations_customer_id_is_404_not_403(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        Account.objects.create(customer=self.customer, name="North America")
        self.client.force_authenticate(other_admin)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_can_add_an_account(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"name": "North America", "domain": "na.globex.com", "lifecycle_stage": "onboarding"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["customer"], self.customer.id)
        account = Account.objects.get(name="North America")
        self.assertEqual(account.customer_id, self.customer.id)
        self.assertEqual(account.domain, "na.globex.com")

    def test_create_only_requires_a_name(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.url, {"name": "North America"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_accepts_an_owner_in_the_same_organisation(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url, {"name": "North America", "owner_id": self.admin.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["owner"]["email"], "alice@acme.io")

    def test_create_rejects_an_owner_from_another_organisation(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_user = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, {"name": "North America", "owner_id": other_user.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Account.objects.filter(name="North America").exists())

    def test_created_account_is_assigned_to_the_url_customer_not_a_supplied_one(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, {"name": "Sneaky", "customer": other_customer.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Account.objects.get(name="Sneaky").customer_id, self.customer.id)

    def test_create_for_a_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/v1/customers/999999/accounts/", {"name": "North America"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AccountDetailTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.account = Account.objects.create(customer=self.customer, name="North America")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/"

    def test_unauthenticated_cannot_view_or_edit(self):
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)
        response = self.client.patch(self.url, {"name": "Renamed"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_can_view_and_edit(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "North America")

        response = self.client.patch(
            self.url, {"name": "North America Enterprise", "lifecycle_stage": "live"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.account.refresh_from_db()
        self.assertEqual(self.account.name, "North America Enterprise")
        self.assertEqual(self.account.lifecycle_stage, "live")

    def test_edit_cannot_move_the_account_to_a_different_customer(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        response = self.client.patch(self.url, {"customer": other_customer.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.account.refresh_from_db()
        self.assertEqual(self.account.customer_id, self.customer.id)

    def test_edit_rejects_an_owner_from_another_organisation(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_user = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(self.url, {"owner_id": other_user.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_wrong_customer_id_in_the_url_is_404_even_for_a_valid_account_id(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        url = f"/api/v1/customers/{other_customer.id}/accounts/{self.account.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

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
        self.account.refresh_from_db()
        self.assertEqual(self.account.name, "North America")


class CustomerActivityListTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.url = f"/api/v1/customers/{self.customer.id}/activities/"

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_customers_activities_with_type_display_and_plain_array(self):
        Activity.objects.create(
            customer=self.customer,
            type=Activity.ActivityType.SUCCESS_PLAN_CREATED,
            occurred_at="2026-03-01",
            links=2,
            watchers=1,
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(response.data[0]["type_display"], "Success Plan Created")
        self.assertEqual(response.data[0]["links"], 2)
        self.assertEqual(response.data[0]["watchers"], 1)

    def test_does_not_include_an_accounts_activities(self):
        account = Account.objects.create(customer=self.customer, name="North America")
        Activity.objects.create(
            account=account, type=Activity.ActivityType.OTHER, occurred_at="2026-03-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_customers_activities(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        Activity.objects.create(
            customer=other_customer, type=Activity.ActivityType.OTHER, occurred_at="2026-03-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/activities/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_another_organisations_customer_id_is_404(self):
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


class AccountActivityListTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.account = Account.objects.create(customer=self.customer, name="North America")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/activities/"

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_accounts_activities(self):
        Activity.objects.create(
            account=self.account,
            type=Activity.ActivityType.EXECUTIVE_ALIGNMENT_SESSION,
            occurred_at="2026-03-20",
            links=0,
            watchers=3,
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["type_display"], "Executive Alignment Session")
        self.assertEqual(response.data[0]["watchers"], 3)

    def test_does_not_include_the_customers_own_activities(self):
        Activity.objects.create(
            customer=self.customer, type=Activity.ActivityType.OTHER, occurred_at="2026-03-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_accounts_activities(self):
        other_account = Account.objects.create(customer=self.customer, name="EMEA")
        Activity.objects.create(
            account=other_account, type=Activity.ActivityType.OTHER, occurred_at="2026-03-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_wrong_customer_id_in_the_url_is_404_even_for_a_valid_account_id(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        url = f"/api/v1/customers/{other_customer.id}/accounts/{self.account.id}/activities/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_another_organisations_admin_gets_404(self):
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
