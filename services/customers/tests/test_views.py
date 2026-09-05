"""Integration tier: through the real URLconf + real test DB."""

from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import (
    Account,
    Activity,
    CalendarEvent,
    Canvas,
    Contact,
    Customer,
    Email,
    Note,
    Opportunity,
    Risk,
    Survey,
    Task,
    Ticket,
)


def create_account(customer, **kwargs):
    """Account.customers is a many-to-many now (see that model's own
    docstring), so `Account.objects.create(customer=...)` no longer
    works — this is the test-suite's own equivalent, linking `customer`
    onto the new Account right after creating it."""
    account = Account.objects.create(**kwargs)
    account.customers.add(customer)
    return account


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
                "email": "contact@globex.com",
                "phone": "+1 (555) 010-2030",
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
        self.assertEqual(customer.email, "contact@globex.com")
        self.assertEqual(customer.phone, "+1 (555) 010-2030")
        self.assertEqual(str(customer.total_contract_value), "179500.00")

    def test_csm_can_also_list_and_create(self):
        """Unlike User Management, customer records aren't admin-gated."""
        self.client.force_authenticate(self.csm)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.post(self.url, {"name": "Initech"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_defaults_currency_to_the_orgs_own_currency(self):
        self.org.currency = "GBP"
        self.org.save()
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.url, {"name": "Initech"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["currency"], "GBP")
        self.assertEqual(response.data["currency_display"], "British Pound (£)")

    def test_create_accepts_an_explicit_currency_different_from_the_orgs_own(self):
        # A US-HQ org can still bill one particular customer in EUR.
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url, {"name": "Globex EU", "currency": "EUR"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["currency"], "EUR")
        self.assertEqual(Customer.objects.get(name="Globex EU").currency, "EUR")

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

    def test_converts_a_non_base_currency_customer_using_the_configured_rate(self):
        from services.fx_rates.models import FxRate

        FxRate.objects.create(organisation=self.org, currency="EUR", rate_to_org_currency="2.0")
        Customer.objects.create(
            organisation=self.org,
            name="Euro Co",
            health_score="9.0",
            currency="EUR",
            arr_billed_at_account="1000.00",
        )

        response = self.client.get(self.url)

        good = response.data["health"]["good"]
        self.assertEqual(good["count"], 1)
        self.assertEqual(good["arr"], 2000.0)  # 1000 EUR * 2.0 -> 2000 (org's own currency)
        self.assertEqual(response.data["unconverted_count"], 0)

    def test_excludes_but_still_counts_a_customer_with_no_configured_rate(self):
        Customer.objects.create(
            organisation=self.org,
            name="Euro Co",
            health_score="9.0",
            currency="EUR",
            arr_billed_at_account="1000.00",
        )
        Customer.objects.create(
            organisation=self.org,
            name="USD Co",
            health_score="9.0",
            arr_billed_at_account="500.00",
        )

        response = self.client.get(self.url)

        good = response.data["health"]["good"]
        # Both customers counted...
        self.assertEqual(good["count"], 2)
        # ...but only the one whose currency actually converts contributes
        # to the money totals -- never a silently-wrong number that treats
        # 1000 EUR as if it were 1000 of the org's own currency.
        self.assertEqual(good["arr"], 500.0)
        self.assertEqual(response.data["unconverted_count"], 1)


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
        create_account(self.customer, name="North America", health_score=8.5)
        create_account(self.customer, name="EMEA", health_score=6.0)
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = {row["name"] for row in response.data}
        self.assertEqual(names, {"North America", "EMEA"})

    def test_response_is_a_plain_list_not_paginated(self):
        create_account(self.customer, name="North America")
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)
        self.assertIsInstance(response.data, list)

    def test_includes_derived_health_category_and_nested_owner(self):
        create_account(self.customer, name="North America", health_score=8.5, owner=self.admin)
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
        create_account(other_customer, name="Initech HQ")
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
        create_account(self.customer, name="North America")
        self.client.force_authenticate(other_admin)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_can_add_an_account(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {
                "name": "North America",
                "domain": "na.globex.com",
                "address": "Austin, TX",
                "email": "na@globex.com",
                "phone": "+1 (555) 020-4040",
                "lifecycle_stage": "onboarding",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data["customers"], [{"id": self.customer.id, "name": self.customer.name}]
        )
        account = Account.objects.get(name="North America")
        self.assertEqual(list(account.customers.all()), [self.customer])
        self.assertEqual(account.domain, "na.globex.com")
        self.assertEqual(account.address, "Austin, TX")
        self.assertEqual(account.email, "na@globex.com")
        self.assertEqual(account.phone, "+1 (555) 020-4040")

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
            self.url, {"name": "Sneaky", "customer_ids": [other_customer.id]}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        sneaky = Account.objects.get(name="Sneaky")
        self.assertIn(self.customer, sneaky.customers.all())

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
        self.account = create_account(self.customer, name="North America")
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
        # "customer" (singular) isn't a serializer field at all any more
        # -- only "customer_ids" (plural, see AccountSerializer's own
        # docstring) can change the linked set, so this key is silently
        # ignored rather than doing anything.
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        response = self.client.patch(self.url, {"customer": other_customer.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.account.refresh_from_db()
        self.assertEqual(list(self.account.customers.all()), [self.customer])

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
        account = create_account(self.customer, name="North America")
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
        self.account = create_account(self.customer, name="North America")
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
        other_account = create_account(self.customer, name="EMEA")
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


class CustomerEmailListTests(APITestCase):
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
        self.url = f"/api/v1/customers/{self.customer.id}/emails/"

    def _email_kwargs(self, **overrides):
        kwargs = {
            "subject": "Welcome aboard",
            "sender_name": "Edgar Holmes",
            "recipient_name": "Natalie Reyes",
            "body": "Hi Natalie, excited to get started.",
            "sent_at": "2026-03-05T18:20:00Z",
            "links": 3,
            "watchers": 2,
            "is_starred": True,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_customers_emails_as_a_plain_array(self):
        Email.objects.create(customer=self.customer, **self._email_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(response.data[0]["subject"], "Welcome aboard")
        self.assertEqual(response.data[0]["sender_name"], "Edgar Holmes")
        self.assertEqual(response.data[0]["recipient_name"], "Natalie Reyes")
        self.assertEqual(response.data[0]["links"], 3)
        self.assertEqual(response.data[0]["watchers"], 2)
        self.assertTrue(response.data[0]["is_starred"])

    def test_does_not_include_an_accounts_emails(self):
        account = create_account(self.customer, name="North America")
        Email.objects.create(account=account, **self._email_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_customers_emails(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        Email.objects.create(customer=other_customer, **self._email_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/emails/")
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


class AccountEmailListTests(APITestCase):
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
        self.account = create_account(self.customer, name="North America")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/emails/"

    def _email_kwargs(self, **overrides):
        kwargs = {
            "subject": "Escalation: Critical Integration Issue",
            "sender_name": "Edgar Holmes",
            "recipient_name": "Support Team",
            "body": "Priority escalation.",
            "sent_at": "2026-03-02T08:15:00Z",
            "links": 0,
            "watchers": 5,
            "is_starred": False,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_accounts_emails(self):
        Email.objects.create(account=self.account, **self._email_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["subject"], "Escalation: Critical Integration Issue")
        self.assertEqual(response.data[0]["watchers"], 5)

    def test_does_not_include_the_customers_own_emails(self):
        Email.objects.create(customer=self.customer, **self._email_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_accounts_emails(self):
        other_account = create_account(self.customer, name="EMEA")
        Email.objects.create(account=other_account, **self._email_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_wrong_customer_id_in_the_url_is_404_even_for_a_valid_account_id(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        url = f"/api/v1/customers/{other_customer.id}/accounts/{self.account.id}/emails/"
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


class CustomerTaskListTests(APITestCase):
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
        self.url = f"/api/v1/customers/{self.customer.id}/tasks/"

    def _task_kwargs(self, **overrides):
        kwargs = {
            "title": "Prepare QBR deck",
            "assignee_name": "Edgar Holmes",
            "due_date": "2026-03-15",
            "priority": Task.Priority.HIGH,
            "status": Task.Status.IN_PROGRESS,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_customers_tasks_as_a_plain_array(self):
        Task.objects.create(customer=self.customer, **self._task_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(response.data[0]["title"], "Prepare QBR deck")
        self.assertEqual(response.data[0]["assignee_name"], "Edgar Holmes")
        self.assertEqual(response.data[0]["priority"], "high")
        self.assertEqual(response.data[0]["status"], "in-progress")

    def test_does_not_include_an_accounts_tasks(self):
        account = create_account(self.customer, name="North America")
        Task.objects.create(account=account, **self._task_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_customers_tasks(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        Task.objects.create(customer=other_customer, **self._task_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/tasks/")
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


class AccountTaskListTests(APITestCase):
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
        self.account = create_account(self.customer, name="North America")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/tasks/"

    def _task_kwargs(self, **overrides):
        kwargs = {
            "title": "Resolve integration escalation",
            "assignee_name": "Edgar Holmes",
            "due_date": "2026-03-05",
            "priority": Task.Priority.HIGH,
            "status": Task.Status.IN_PROGRESS,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_accounts_tasks(self):
        Task.objects.create(account=self.account, **self._task_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["title"], "Resolve integration escalation")

    def test_does_not_include_the_customers_own_tasks(self):
        Task.objects.create(customer=self.customer, **self._task_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_accounts_tasks(self):
        other_account = create_account(self.customer, name="EMEA")
        Task.objects.create(account=other_account, **self._task_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_wrong_customer_id_in_the_url_is_404_even_for_a_valid_account_id(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        url = f"/api/v1/customers/{other_customer.id}/accounts/{self.account.id}/tasks/"
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


class CustomerNoteListTests(APITestCase):
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
        self.url = f"/api/v1/customers/{self.customer.id}/notes/"

    def _note_kwargs(self, **overrides):
        kwargs = {
            "title": "Call Notes: Product Feedback Session",
            "author_name": "Edgar Holmes",
            "body": "Customer expressed interest in AI-powered analytics.",
            "logged_at": "2026-03-04",
            "links": 2,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_customers_notes_as_a_plain_array(self):
        Note.objects.create(customer=self.customer, **self._note_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(response.data[0]["title"], "Call Notes: Product Feedback Session")
        self.assertEqual(response.data[0]["author_name"], "Edgar Holmes")
        self.assertEqual(response.data[0]["links"], 2)

    def test_does_not_include_an_accounts_notes(self):
        account = create_account(self.customer, name="North America")
        Note.objects.create(account=account, **self._note_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_customers_notes(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        Note.objects.create(customer=other_customer, **self._note_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/notes/")
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


class AccountNoteListTests(APITestCase):
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
        self.account = create_account(self.customer, name="North America")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/notes/"

    def _note_kwargs(self, **overrides):
        kwargs = {
            "title": "Executive Sponsor Meeting Notes",
            "author_name": "Edgar Holmes",
            "body": "Tim expressed high satisfaction with the platform.",
            "logged_at": "2026-03-20",
            "links": 0,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_accounts_notes(self):
        Note.objects.create(account=self.account, **self._note_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["title"], "Executive Sponsor Meeting Notes")

    def test_does_not_include_the_customers_own_notes(self):
        Note.objects.create(customer=self.customer, **self._note_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_accounts_notes(self):
        other_account = create_account(self.customer, name="EMEA")
        Note.objects.create(account=other_account, **self._note_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_wrong_customer_id_in_the_url_is_404_even_for_a_valid_account_id(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        url = f"/api/v1/customers/{other_customer.id}/accounts/{self.account.id}/notes/"
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


class CustomerTicketListTests(APITestCase):
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
        self.url = f"/api/v1/customers/{self.customer.id}/tickets/"

    def _ticket_kwargs(self, **overrides):
        kwargs = {
            "ticket_number": "TKT-1042",
            "title": "Dashboard loading slow on large datasets",
            "assignee_name": "Support Team",
            "status": Ticket.Status.IN_PROGRESS,
            "priority": Ticket.Priority.HIGH,
            "opened_at": "2026-03-03",
            "links": 2,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_customers_tickets_as_a_plain_array(self):
        Ticket.objects.create(customer=self.customer, **self._ticket_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(response.data[0]["ticket_number"], "TKT-1042")
        self.assertEqual(response.data[0]["status"], "in-progress")
        self.assertEqual(response.data[0]["priority"], "high")
        self.assertEqual(response.data[0]["links"], 2)

    def test_does_not_include_an_accounts_tickets(self):
        account = create_account(self.customer, name="North America")
        Ticket.objects.create(account=account, **self._ticket_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_customers_tickets(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        Ticket.objects.create(customer=other_customer, **self._ticket_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/tickets/")
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


class AccountTicketListTests(APITestCase):
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
        self.account = create_account(self.customer, name="North America")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/tickets/"

    def _ticket_kwargs(self, **overrides):
        kwargs = {
            "ticket_number": "TKT-2001",
            "title": "API rate limit exceeded during batch import",
            "assignee_name": "Engineering",
            "status": Ticket.Status.IN_PROGRESS,
            "priority": Ticket.Priority.HIGH,
            "opened_at": "2026-03-26",
            "links": 1,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_accounts_tickets(self):
        Ticket.objects.create(account=self.account, **self._ticket_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["title"], "API rate limit exceeded during batch import")

    def test_does_not_include_the_customers_own_tickets(self):
        Ticket.objects.create(customer=self.customer, **self._ticket_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_accounts_tickets(self):
        other_account = create_account(self.customer, name="EMEA")
        Ticket.objects.create(account=other_account, **self._ticket_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_wrong_customer_id_in_the_url_is_404_even_for_a_valid_account_id(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        url = f"/api/v1/customers/{other_customer.id}/accounts/{self.account.id}/tickets/"
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


class CustomerCalendarEventListTests(APITestCase):
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
        self.url = f"/api/v1/customers/{self.customer.id}/calendar-events/"

    def _event_kwargs(self, **overrides):
        kwargs = {
            "title": "Quarterly Business Review",
            "description": "Q1 2026 QBR with stakeholders",
            "type": CalendarEvent.EventType.REVIEW,
            "event_date": "2026-03-15",
            "start_time": "10:00",
            "end_time": "11:30",
            "attendee_count": 3,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_customers_events_as_a_plain_array(self):
        CalendarEvent.objects.create(customer=self.customer, **self._event_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(response.data[0]["title"], "Quarterly Business Review")
        self.assertEqual(response.data[0]["type"], "review")
        self.assertEqual(response.data[0]["attendee_count"], 3)

    def test_does_not_include_an_accounts_events(self):
        account = create_account(self.customer, name="North America")
        CalendarEvent.objects.create(account=account, **self._event_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_customers_events(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        CalendarEvent.objects.create(customer=other_customer, **self._event_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/calendar-events/")
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


class AccountCalendarEventListTests(APITestCase):
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
        self.account = create_account(self.customer, name="North America")
        self.url = (
            f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/calendar-events/"
        )

    def _event_kwargs(self, **overrides):
        kwargs = {
            "title": "Q2 Business Review — Executive Session",
            "description": "Exec-level QBR",
            "type": CalendarEvent.EventType.REVIEW,
            "event_date": "2026-04-05",
            "start_time": "10:00",
            "end_time": "11:30",
            "attendee_count": 3,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_accounts_events(self):
        CalendarEvent.objects.create(account=self.account, **self._event_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["title"], "Q2 Business Review — Executive Session")

    def test_does_not_include_the_customers_own_events(self):
        CalendarEvent.objects.create(customer=self.customer, **self._event_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_accounts_events(self):
        other_account = create_account(self.customer, name="EMEA")
        CalendarEvent.objects.create(account=other_account, **self._event_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_wrong_customer_id_in_the_url_is_404_even_for_a_valid_account_id(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        url = f"/api/v1/customers/{other_customer.id}/accounts/{self.account.id}/calendar-events/"
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


class CustomerContactListTests(APITestCase):
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
        self.url = f"/api/v1/customers/{self.customer.id}/contacts/"

    def _contact_kwargs(self, **overrides):
        kwargs = {
            "name": "Sarah Chen",
            "role": Contact.Role.EXECUTIVE_SPONSOR,
            "email": "sarah.chen@globex.com",
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_customers_contacts_as_a_plain_array(self):
        Contact.objects.create(customer=self.customer, **self._contact_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(response.data[0]["name"], "Sarah Chen")
        self.assertEqual(response.data[0]["role"], "executive_sponsor")
        self.assertEqual(response.data[0]["role_display"], "Executive Sponsor")

    def test_rolls_up_this_customers_own_accounts_contacts_too(self):
        # An Account can have its own individual contacts, separate
        # from the Customer's own organisation-level ones — the
        # Organization Details page's own Contacts tab shows both
        # together (see CustomerContactListView's own docstring).
        account = create_account(self.customer, name="North America")
        Contact.objects.create(
            customer=self.customer, **self._contact_kwargs(name="Org-Level Contact")
        )
        Contact.objects.create(
            account=account, **self._contact_kwargs(name="Account-Level Contact")
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        names = {row["name"] for row in response.data}
        self.assertEqual(names, {"Org-Level Contact", "Account-Level Contact"})
        account_row = next(row for row in response.data if row["name"] == "Account-Level Contact")
        self.assertEqual(account_row["account_name"], "North America")
        org_row = next(row for row in response.data if row["name"] == "Org-Level Contact")
        self.assertIsNone(org_row["account_name"])

    def test_does_not_leak_another_customers_accounts_contacts(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        other_account = create_account(other_customer, name="Other Region")
        Contact.objects.create(account=other_account, **self._contact_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_customers_contacts(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        Contact.objects.create(customer=other_customer, **self._contact_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/contacts/")
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

    def test_admin_can_add_an_organization_level_contact(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, self._contact_kwargs(name="New Contact"), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        contact = Contact.objects.get(pk=response.data["id"])
        self.assertEqual(contact.customer, self.customer)
        self.assertIsNone(contact.account)
        self.assertEqual(
            response.data["companies"], [{"id": self.customer.id, "name": self.customer.name}]
        )

    def test_cannot_add_a_contact_to_another_organisations_customer(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(other_admin)

        response = self.client.post(self.url, self._contact_kwargs(), format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AccountContactListTests(APITestCase):
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
        self.account = create_account(self.customer, name="North America")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/contacts/"

    def _contact_kwargs(self, **overrides):
        kwargs = {
            "name": "James Wilson",
            "role": Contact.Role.CHAMPION,
            "email": "j.wilson@globex.com",
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_accounts_contacts(self):
        Contact.objects.create(account=self.account, **self._contact_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["name"], "James Wilson")

    def test_does_not_include_the_customers_own_contacts(self):
        Contact.objects.create(customer=self.customer, **self._contact_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_accounts_contacts(self):
        other_account = create_account(self.customer, name="EMEA")
        Contact.objects.create(account=other_account, **self._contact_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_wrong_customer_id_in_the_url_is_404_even_for_a_valid_account_id(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        url = f"/api/v1/customers/{other_customer.id}/accounts/{self.account.id}/contacts/"
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

    def test_admin_can_add_an_account_level_contact(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, self._contact_kwargs(name="New Contact"), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        contact = Contact.objects.get(pk=response.data["id"])
        self.assertEqual(contact.account, self.account)
        self.assertIsNone(contact.customer)
        self.assertEqual(response.data["account_name"], "North America")


class ContactListTests(APITestCase):
    """/api/v1/contacts/ — the one Contact view not nested under a
    single Customer/Account (see ContactListView's own docstring)."""

    url = "/api/v1/contacts/"

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
        self.account = create_account(self.customer, name="North America")

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_both_org_level_and_account_level_contacts(self):
        Contact.objects.create(
            customer=self.customer,
            name="Sarah Chen",
            role=Contact.Role.EXECUTIVE_SPONSOR,
            email="sarah.chen@globex.com",
        )
        Contact.objects.create(
            account=self.account,
            name="James Wilson",
            role=Contact.Role.CHAMPION,
            email="j.wilson@globex.com",
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        names = {row["name"] for row in response.data["results"]}
        self.assertEqual(names, {"Sarah Chen", "James Wilson"})

    def test_response_includes_company_and_account_name(self):
        Contact.objects.create(
            account=self.account,
            name="James Wilson",
            role=Contact.Role.CHAMPION,
            email="j.wilson@globex.com",
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        row = response.data["results"][0]
        self.assertEqual(row["companies"], [{"id": self.customer.id, "name": "Globex"}])
        self.assertEqual(row["account_name"], self.account.name)
        self.assertEqual(row["account_name"], "North America")

    def test_org_level_contact_has_no_account_name(self):
        Contact.objects.create(
            customer=self.customer,
            name="Sarah Chen",
            role=Contact.Role.EXECUTIVE_SPONSOR,
            email="sarah.chen@globex.com",
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertIsNone(response.data["results"][0]["account_name"])

    def test_search_matches_name(self):
        Contact.objects.create(
            customer=self.customer,
            name="Sarah Chen",
            role=Contact.Role.EXECUTIVE_SPONSOR,
            email="sarah.chen@globex.com",
        )
        Contact.objects.create(
            customer=self.customer,
            name="James Wilson",
            role=Contact.Role.CHAMPION,
            email="j.wilson@globex.com",
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url, {"search": "sarah"})

        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "Sarah Chen")

    def test_company_filter_matches_account_level_contacts_too(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        Contact.objects.create(
            customer=other_customer,
            name="Peter Gibbons",
            role=Contact.Role.INFLUENCER,
            email="peter@initech.com",
        )
        Contact.objects.create(
            account=self.account,
            name="James Wilson",
            role=Contact.Role.CHAMPION,
            email="j.wilson@globex.com",
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url, {"company": self.customer.id})

        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "James Wilson")

    def test_does_not_leak_another_organisations_contacts(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        other_customer = Customer.objects.create(organisation=other_org, name="Other Co")
        Contact.objects.create(
            customer=other_customer,
            name="Someone Else",
            role=Contact.Role.OTHER,
            email="someone@otherco.com",
        )
        Contact.objects.create(
            customer=self.customer,
            name="Sarah Chen",
            role=Contact.Role.EXECUTIVE_SPONSOR,
            email="sarah.chen@globex.com",
        )
        self.client.force_authenticate(other_admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "Someone Else")


class ContactStatsTests(APITestCase):
    url = "/api/v1/contacts/stats/"

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

    def test_unauthenticated_cannot_view(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_totals_active_and_sentiment_breakdown(self):
        Contact.objects.create(
            customer=self.customer,
            name="A",
            role=Contact.Role.CHAMPION,
            email="a@globex.com",
            status=Contact.Status.ACTIVE,
            sentiment=Contact.Sentiment.POSITIVE,
        )
        Contact.objects.create(
            customer=self.customer,
            name="B",
            role=Contact.Role.CHAMPION,
            email="b@globex.com",
            status=Contact.Status.ACTIVE,
            sentiment=Contact.Sentiment.NEGATIVE,
        )
        Contact.objects.create(
            customer=self.customer,
            name="C",
            role=Contact.Role.CHAMPION,
            email="c@globex.com",
            status=Contact.Status.INACTIVE,
            sentiment=Contact.Sentiment.NEUTRAL,
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total"], 3)
        self.assertEqual(response.data["active"], 2)
        self.assertEqual(response.data["sentiment"]["positive"], 1)
        self.assertEqual(response.data["sentiment"]["negative"], 1)
        self.assertEqual(response.data["sentiment"]["neutral"], 1)

    def test_growth_is_none_when_nothing_existed_30_days_ago(self):
        Contact.objects.create(
            customer=self.customer,
            name="A",
            role=Contact.Role.CHAMPION,
            email="a@globex.com",
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertIsNone(response.data["growth_30d_pct"])

    def test_growth_compares_against_the_30_day_old_total(self):
        old = Contact.objects.create(
            customer=self.customer,
            name="A",
            role=Contact.Role.CHAMPION,
            email="a@globex.com",
        )
        Contact.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=45))
        Contact.objects.create(
            customer=self.customer,
            name="B",
            role=Contact.Role.CHAMPION,
            email="b@globex.com",
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        # 1 contact existed 30+ days ago, 2 exist now -> +100%.
        self.assertEqual(response.data["growth_30d_pct"], 100.0)

    def test_does_not_count_another_organisations_contacts(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        other_customer = Customer.objects.create(organisation=other_org, name="Other Co")
        Contact.objects.create(
            customer=other_customer,
            name="Someone",
            role=Contact.Role.OTHER,
            email="someone@otherco.com",
        )
        self.client.force_authenticate(other_admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data["total"], 1)


class ContactDetailTests(APITestCase):
    """GET/PATCH/DELETE /api/v1/contacts/<id>/ — flat, not nested under
    a Customer/Account (see ContactDetailView's own docstring). Covers
    both an organization-level and an account-level Contact, since the
    scoping query has to cover both shapes."""

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
        self.account = create_account(self.customer, name="North America")
        self.org_contact = Contact.objects.create(
            customer=self.customer,
            name="Sarah Chen",
            role=Contact.Role.EXECUTIVE_SPONSOR,
            email="sarah.chen@globex.com",
        )
        self.account_contact = Contact.objects.create(
            account=self.account,
            name="James Wilson",
            role=Contact.Role.CHAMPION,
            email="j.wilson@globex.com",
        )

    def test_unauthenticated_cannot_view(self):
        response = self.client.get(f"/api/v1/contacts/{self.org_contact.id}/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_can_retrieve_an_organization_level_contact(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/v1/contacts/{self.org_contact.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Sarah Chen")

    def test_can_retrieve_an_account_level_contact(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/v1/contacts/{self.account_contact.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "James Wilson")
        self.assertEqual(response.data["account_name"], "North America")

    def test_can_update_an_organization_level_contact(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/contacts/{self.org_contact.id}/",
            {"name": "Sarah Chen-Wu", "status": "inactive"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.org_contact.refresh_from_db()
        self.assertEqual(self.org_contact.name, "Sarah Chen-Wu")
        self.assertEqual(self.org_contact.status, Contact.Status.INACTIVE)

    def test_updating_cannot_move_a_contact_between_parents(self):
        # customer/account aren't in ContactSerializer's own `fields` at
        # all, so naming either in the PATCH body is silently ignored,
        # not an error.
        self.client.force_authenticate(self.admin)
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")

        response = self.client.patch(
            f"/api/v1/contacts/{self.org_contact.id}/",
            {"customer": other_customer.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.org_contact.refresh_from_db()
        self.assertEqual(self.org_contact.customer, self.customer)

    def test_can_delete_a_contact(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"/api/v1/contacts/{self.org_contact.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Contact.objects.filter(pk=self.org_contact.id).exists())

    def test_another_organisations_admin_gets_404_for_view_update_and_delete(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(other_admin)
        url = f"/api/v1/contacts/{self.org_contact.id}/"

        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            self.client.patch(url, {"name": "Hijacked"}, format="json").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_404_NOT_FOUND)
        # Confirmed nothing actually happened to it.
        self.org_contact.refresh_from_db()
        self.assertEqual(self.org_contact.name, "Sarah Chen")

    def test_nonexistent_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/contacts/999999/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CustomerOpportunityListTests(APITestCase):
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
        self.url = f"/api/v1/customers/{self.customer.id}/opportunities/"

    def _opportunity_kwargs(self, **overrides):
        kwargs = {
            "title": "Renewal Expansion Opportunity",
            "mrr": "2500.00",
            "stage": Opportunity.Stage.DISCOVERY,
            "priority": Opportunity.Priority.HIGH,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_customers_opportunities_as_a_plain_array(self):
        Opportunity.objects.create(customer=self.customer, **self._opportunity_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(response.data[0]["title"], "Renewal Expansion Opportunity")
        self.assertEqual(response.data[0]["stage"], "discovery")
        self.assertEqual(response.data[0]["stage_display"], "Discovery")
        self.assertEqual(response.data[0]["mrr"], "2500.00")

    def test_rolls_up_this_customers_own_accounts_opportunities_too(self):
        account = create_account(self.customer, name="North America")
        Opportunity.objects.create(
            customer=self.customer, **self._opportunity_kwargs(title="Org-Level Opp")
        )
        Opportunity.objects.create(
            account=account, **self._opportunity_kwargs(title="Account-Level Opp")
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        titles = {row["title"] for row in response.data}
        self.assertEqual(titles, {"Org-Level Opp", "Account-Level Opp"})
        account_row = next(row for row in response.data if row["title"] == "Account-Level Opp")
        self.assertEqual(account_row["account_name"], "North America")

    def test_does_not_leak_another_customers_opportunities(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        Opportunity.objects.create(customer=other_customer, **self._opportunity_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/opportunities/")
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

    def test_admin_can_add_an_organization_level_opportunity(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, self._opportunity_kwargs(title="New Opp"), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        opportunity = Opportunity.objects.get(pk=response.data["id"])
        self.assertEqual(opportunity.customer, self.customer)
        self.assertIsNone(opportunity.account)


class AccountOpportunityListTests(APITestCase):
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
        self.account = create_account(self.customer, name="North America")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/opportunities/"

    def _opportunity_kwargs(self, **overrides):
        kwargs = {
            "title": "Q2 Business Review — Executive Session",
            "mrr": "1500.00",
            "stage": Opportunity.Stage.NEGOTIATION,
            "priority": Opportunity.Priority.MEDIUM,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_accounts_opportunities(self):
        Opportunity.objects.create(account=self.account, **self._opportunity_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["title"], "Q2 Business Review — Executive Session")

    def test_does_not_include_the_customers_own_opportunities(self):
        Opportunity.objects.create(customer=self.customer, **self._opportunity_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_accounts_opportunities(self):
        other_account = create_account(self.customer, name="EMEA")
        Opportunity.objects.create(account=other_account, **self._opportunity_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_wrong_customer_id_in_the_url_is_404_even_for_a_valid_account_id(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        url = f"/api/v1/customers/{other_customer.id}/accounts/{self.account.id}/opportunities/"
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

    def test_admin_can_add_an_account_level_opportunity(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, self._opportunity_kwargs(title="New Opp"), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        opportunity = Opportunity.objects.get(pk=response.data["id"])
        self.assertEqual(opportunity.account, self.account)
        self.assertIsNone(opportunity.customer)
        self.assertEqual(response.data["account_name"], "North America")


class OpportunityListTests(APITestCase):
    """/api/v1/opportunities/ — the one Opportunity view not nested
    under a single Customer/Account (see OpportunityListView's own
    docstring). Powers the standalone Pipelines board."""

    url = "/api/v1/opportunities/"

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
        self.account = create_account(self.customer, name="North America")

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_both_org_level_and_account_level_opportunities_as_a_plain_array(self):
        Opportunity.objects.create(
            customer=self.customer,
            title="Org Opp",
            mrr="1000.00",
        )
        Opportunity.objects.create(
            account=self.account,
            title="Account Opp",
            mrr="2000.00",
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        titles = {row["title"] for row in response.data}
        self.assertEqual(titles, {"Org Opp", "Account Opp"})

    def test_does_not_leak_another_organisations_opportunities(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        other_customer = Customer.objects.create(organisation=other_org, name="Other Co")
        Opportunity.objects.create(
            customer=other_customer, title="Someone Else's Opp", mrr="500.00"
        )
        Opportunity.objects.create(customer=self.customer, title="Globex Opp", mrr="500.00")
        self.client.force_authenticate(other_admin)

        response = self.client.get(self.url)

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["title"], "Someone Else's Opp")

    def test_posting_with_customer_id_creates_an_organization_level_opportunity(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"title": "New Opp", "mrr": "1000.00", "customer_id": self.customer.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        opportunity = Opportunity.objects.get(pk=response.data["id"])
        self.assertEqual(opportunity.customer, self.customer)
        self.assertIsNone(opportunity.account)

    def test_posting_with_account_id_creates_an_account_level_opportunity(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"title": "New Opp", "mrr": "1000.00", "account_id": self.account.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        opportunity = Opportunity.objects.get(pk=response.data["id"])
        self.assertEqual(opportunity.account, self.account)
        self.assertIsNone(opportunity.customer)

    def test_posting_with_neither_customer_id_nor_account_id_is_400(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url, {"title": "New Opp", "mrr": "1000.00"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_post_against_another_organisations_customer_id(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_customer = Customer.objects.create(organisation=other_org, name="Other Co")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"title": "New Opp", "mrr": "1000.00", "customer_id": other_customer.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class OpportunityDetailTests(APITestCase):
    """GET/PATCH/DELETE /api/v1/opportunities/<id>/ — flat, not nested
    under a Customer/Account (see OpportunityDetailView's own
    docstring). Covers both an organization-level and an account-level
    Opportunity, since the scoping query has to cover both shapes."""

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
        self.account = create_account(self.customer, name="North America")
        self.org_opportunity = Opportunity.objects.create(
            customer=self.customer,
            title="Org Opp",
            mrr="1000.00",
            stage=Opportunity.Stage.DISCOVERY,
        )
        self.account_opportunity = Opportunity.objects.create(
            account=self.account,
            title="Account Opp",
            mrr="2000.00",
            stage=Opportunity.Stage.NEGOTIATION,
        )

    def test_unauthenticated_cannot_view(self):
        response = self.client.get(f"/api/v1/opportunities/{self.org_opportunity.id}/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_can_retrieve_an_organization_level_opportunity(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/v1/opportunities/{self.org_opportunity.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Org Opp")

    def test_can_retrieve_an_account_level_opportunity(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/v1/opportunities/{self.account_opportunity.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["account_name"], "North America")

    def test_can_update_the_stage_drag_and_drop(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/opportunities/{self.org_opportunity.id}/",
            {"stage": "closed_won"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.org_opportunity.refresh_from_db()
        self.assertEqual(self.org_opportunity.stage, Opportunity.Stage.CLOSED_WON)

    def test_updating_cannot_move_an_opportunity_between_parents(self):
        self.client.force_authenticate(self.admin)
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")

        response = self.client.patch(
            f"/api/v1/opportunities/{self.org_opportunity.id}/",
            {"customer": other_customer.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.org_opportunity.refresh_from_db()
        self.assertEqual(self.org_opportunity.customer, self.customer)

    def test_can_delete_an_opportunity(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"/api/v1/opportunities/{self.org_opportunity.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Opportunity.objects.filter(pk=self.org_opportunity.id).exists())

    def test_another_organisations_admin_gets_404_for_view_update_and_delete(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(other_admin)
        url = f"/api/v1/opportunities/{self.org_opportunity.id}/"

        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            self.client.patch(url, {"title": "Hijacked"}, format="json").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_404_NOT_FOUND)
        self.org_opportunity.refresh_from_db()
        self.assertEqual(self.org_opportunity.title, "Org Opp")

    def test_nonexistent_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/opportunities/999999/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CustomerRiskListTests(APITestCase):
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
        self.url = f"/api/v1/customers/{self.customer.id}/risks/"

    def _risk_kwargs(self, **overrides):
        kwargs = {
            "title": "Renewal Risk — Contract Expiry",
            "mrr": "2500.00",
            "stage": Risk.Stage.OPEN,
            "priority": Risk.Priority.HIGH,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_customers_risks_as_a_plain_array(self):
        Risk.objects.create(customer=self.customer, **self._risk_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(response.data[0]["title"], "Renewal Risk — Contract Expiry")
        self.assertEqual(response.data[0]["stage"], "open")
        self.assertEqual(response.data[0]["stage_display"], "Open")
        self.assertEqual(response.data[0]["mrr"], "2500.00")

    def test_rolls_up_this_customers_own_accounts_risks_too(self):
        account = create_account(self.customer, name="North America")
        Risk.objects.create(customer=self.customer, **self._risk_kwargs(title="Org-Level Risk"))
        Risk.objects.create(account=account, **self._risk_kwargs(title="Account-Level Risk"))
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        titles = {row["title"] for row in response.data}
        self.assertEqual(titles, {"Org-Level Risk", "Account-Level Risk"})
        account_row = next(row for row in response.data if row["title"] == "Account-Level Risk")
        self.assertEqual(account_row["account_name"], "North America")

    def test_does_not_leak_another_customers_risks(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        Risk.objects.create(customer=other_customer, **self._risk_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/risks/")
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

    def test_admin_can_add_an_organization_level_risk(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url, self._risk_kwargs(title="New Risk"), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        risk = Risk.objects.get(pk=response.data["id"])
        self.assertEqual(risk.customer, self.customer)
        self.assertIsNone(risk.account)


class AccountRiskListTests(APITestCase):
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
        self.account = create_account(self.customer, name="North America")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/risks/"

    def _risk_kwargs(self, **overrides):
        kwargs = {
            "title": "Disengagement Risk Q1",
            "mrr": "1500.00",
            "stage": Risk.Stage.MITIGATED,
            "priority": Risk.Priority.MEDIUM,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_accounts_risks(self):
        Risk.objects.create(account=self.account, **self._risk_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["title"], "Disengagement Risk Q1")

    def test_does_not_include_the_customers_own_risks(self):
        Risk.objects.create(customer=self.customer, **self._risk_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_accounts_risks(self):
        other_account = create_account(self.customer, name="EMEA")
        Risk.objects.create(account=other_account, **self._risk_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_wrong_customer_id_in_the_url_is_404_even_for_a_valid_account_id(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        url = f"/api/v1/customers/{other_customer.id}/accounts/{self.account.id}/risks/"
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

    def test_admin_can_add_an_account_level_risk(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url, self._risk_kwargs(title="New Risk"), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        risk = Risk.objects.get(pk=response.data["id"])
        self.assertEqual(risk.account, self.account)
        self.assertIsNone(risk.customer)
        self.assertEqual(response.data["account_name"], "North America")


class RiskListTests(APITestCase):
    """/api/v1/risks/ — the one Risk view not nested under a single
    Customer/Account (see RiskListView's own docstring). Powers the
    standalone Pipelines board's "Risks" tab."""

    url = "/api/v1/risks/"

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
        self.account = create_account(self.customer, name="North America")

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_both_org_level_and_account_level_risks_as_a_plain_array(self):
        Risk.objects.create(customer=self.customer, title="Org Risk", mrr="1000.00")
        Risk.objects.create(account=self.account, title="Account Risk", mrr="2000.00")
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        titles = {row["title"] for row in response.data}
        self.assertEqual(titles, {"Org Risk", "Account Risk"})

    def test_does_not_leak_another_organisations_risks(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        other_customer = Customer.objects.create(organisation=other_org, name="Other Co")
        Risk.objects.create(customer=other_customer, title="Someone Else's Risk", mrr="500.00")
        Risk.objects.create(customer=self.customer, title="Globex Risk", mrr="500.00")
        self.client.force_authenticate(other_admin)

        response = self.client.get(self.url)

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["title"], "Someone Else's Risk")

    def test_posting_with_customer_id_creates_an_organization_level_risk(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"title": "New Risk", "mrr": "1000.00", "customer_id": self.customer.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        risk = Risk.objects.get(pk=response.data["id"])
        self.assertEqual(risk.customer, self.customer)
        self.assertIsNone(risk.account)

    def test_posting_with_account_id_creates_an_account_level_risk(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"title": "New Risk", "mrr": "1000.00", "account_id": self.account.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        risk = Risk.objects.get(pk=response.data["id"])
        self.assertEqual(risk.account, self.account)
        self.assertIsNone(risk.customer)

    def test_posting_with_neither_customer_id_nor_account_id_is_400(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, {"title": "New Risk", "mrr": "1000.00"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_post_against_another_organisations_customer_id(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_customer = Customer.objects.create(organisation=other_org, name="Other Co")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"title": "New Risk", "mrr": "1000.00", "customer_id": other_customer.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class RiskDetailTests(APITestCase):
    """GET/PATCH/DELETE /api/v1/risks/<id>/ — flat, not nested under a
    Customer/Account (see RiskDetailView's own docstring). Covers both
    an organization-level and an account-level Risk, since the scoping
    query has to cover both shapes."""

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
        self.account = create_account(self.customer, name="North America")
        self.org_risk = Risk.objects.create(
            customer=self.customer,
            title="Org Risk",
            mrr="1000.00",
            stage=Risk.Stage.OPEN,
        )
        self.account_risk = Risk.objects.create(
            account=self.account,
            title="Account Risk",
            mrr="2000.00",
            stage=Risk.Stage.MITIGATED,
        )

    def test_unauthenticated_cannot_view(self):
        response = self.client.get(f"/api/v1/risks/{self.org_risk.id}/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_can_retrieve_an_organization_level_risk(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/v1/risks/{self.org_risk.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Org Risk")

    def test_can_retrieve_an_account_level_risk(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/v1/risks/{self.account_risk.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["account_name"], "North America")

    def test_can_update_the_stage_drag_and_drop(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/risks/{self.org_risk.id}/",
            {"stage": "realised"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.org_risk.refresh_from_db()
        self.assertEqual(self.org_risk.stage, Risk.Stage.REALISED)

    def test_updating_cannot_move_a_risk_between_parents(self):
        self.client.force_authenticate(self.admin)
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")

        response = self.client.patch(
            f"/api/v1/risks/{self.org_risk.id}/",
            {"customer": other_customer.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.org_risk.refresh_from_db()
        self.assertEqual(self.org_risk.customer, self.customer)

    def test_can_delete_a_risk(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"/api/v1/risks/{self.org_risk.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Risk.objects.filter(pk=self.org_risk.id).exists())

    def test_another_organisations_admin_gets_404_for_view_update_and_delete(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(other_admin)
        url = f"/api/v1/risks/{self.org_risk.id}/"

        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            self.client.patch(url, {"title": "Hijacked"}, format="json").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_404_NOT_FOUND)
        self.org_risk.refresh_from_db()
        self.assertEqual(self.org_risk.title, "Org Risk")

    def test_nonexistent_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/risks/999999/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CustomerSurveyListTests(APITestCase):
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
        self.url = f"/api/v1/customers/{self.customer.id}/surveys/"

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_can_add_an_organization_level_survey(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, {"survey_type": "nps", "sent_at": "2026-09-01"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        survey = Survey.objects.get(pk=response.data["id"])
        self.assertEqual(survey.customer, self.customer)
        self.assertIsNone(survey.account)
        self.assertEqual(survey.status, Survey.Status.SENT)

    def test_rolls_up_this_customers_own_accounts_surveys_too(self):
        account = create_account(self.customer, name="North America")
        Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        Survey.objects.create(
            account=account, survey_type=Survey.SurveyType.CSAT, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        types = {row["survey_type"] for row in response.data}
        self.assertEqual(types, {"nps", "csat"})

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/surveys/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AccountSurveyListTests(APITestCase):
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
        self.account = create_account(self.customer, name="North America")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/surveys/"

    def test_admin_can_add_an_account_level_survey(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, {"survey_type": "csat", "sent_at": "2026-09-01"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        survey = Survey.objects.get(pk=response.data["id"])
        self.assertEqual(survey.account, self.account)
        self.assertIsNone(survey.customer)
        # Unlike Opportunity/Risk, account_id is a real field here — the
        # standalone Surveys page's own row-click navigation needs it.
        self.assertEqual(response.data["account_id"], self.account.id)

    def test_ces_is_rejected_for_an_account(self):
        # Account has no ces_percentage field to sync a response onto —
        # see Survey model's own docstring.
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, {"survey_type": "ces", "sent_at": "2026-09-01"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Survey.objects.count(), 0)


class SurveyListTests(APITestCase):
    """/api/v1/surveys/ — the one Survey view not nested under a single
    Customer/Account (see SurveyListView's own docstring). Powers the
    standalone Surveys page."""

    url = "/api/v1/surveys/"

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
        self.account = create_account(self.customer, name="North America")

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_both_org_level_and_account_level_surveys_as_a_plain_array(self):
        Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        Survey.objects.create(
            account=self.account, survey_type=Survey.SurveyType.CSAT, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        types = {row["survey_type"] for row in response.data}
        self.assertEqual(types, {"nps", "csat"})

    def test_does_not_leak_another_organisations_surveys(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_customer = Customer.objects.create(organisation=other_org, name="Other Co")
        Survey.objects.create(
            customer=other_customer, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.CSAT, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["survey_type"], "csat")

    def test_posting_with_customer_id_creates_an_organization_level_survey(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"survey_type": "nps", "sent_at": "2026-09-01", "customer_id": self.customer.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        survey = Survey.objects.get(pk=response.data["id"])
        self.assertEqual(survey.customer, self.customer)
        self.assertIsNone(survey.account)

    def test_posting_with_account_id_creates_an_account_level_survey(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"survey_type": "csat", "sent_at": "2026-09-01", "account_id": self.account.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        survey = Survey.objects.get(pk=response.data["id"])
        self.assertEqual(survey.account, self.account)
        self.assertIsNone(survey.customer)

    def test_ces_is_rejected_when_posting_with_account_id(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"survey_type": "ces", "sent_at": "2026-09-01", "account_id": self.account.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Survey.objects.count(), 0)

    def test_posting_with_neither_customer_id_nor_account_id_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url, {"survey_type": "nps", "sent_at": "2026-09-01"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SurveyDetailTests(APITestCase):
    """GET/PATCH/DELETE /api/v1/surveys/<id>/ — flat, not nested.
    Covers "Log Response" (PATCH status/score) and the score-sync onto
    the parent Customer/Account, the one real behavior this whole
    feature is for."""

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
        self.account = create_account(self.customer, name="North America")

    def _url(self, survey):
        return f"/api/v1/surveys/{survey.id}/"

    def test_unauthenticated_cannot_view(self):
        survey = Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        response = self.client.get(self._url(survey))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_score_is_required_to_mark_responded(self):
        survey = Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(self._url(survey), {"status": "responded"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_score_out_of_range_for_nps_is_rejected(self):
        survey = Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            self._url(survey), {"status": "responded", "score": 150}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_score_out_of_range_for_csat_is_rejected(self):
        survey = Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.CSAT, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            self._url(survey), {"status": "responded", "score": -5}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_responding_to_a_customer_nps_survey_syncs_customer_nps_score(self):
        survey = Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            self._url(survey), {"status": "responded", "score": 80}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        survey.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(survey.status, Survey.Status.RESPONDED)
        self.assertEqual(survey.score, 80)
        self.assertIsNotNone(survey.responded_at)
        self.assertEqual(self.customer.nps_score, 80)

    def test_responding_to_a_customer_csat_survey_syncs_customer_csat_score(self):
        survey = Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.CSAT, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        self.client.patch(self._url(survey), {"status": "responded", "score": 90}, format="json")

        self.customer.refresh_from_db()
        self.assertEqual(str(self.customer.csat_score), "90.00")

    def test_responding_to_a_customer_ces_survey_syncs_customer_ces_percentage(self):
        survey = Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.CES, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        self.client.patch(self._url(survey), {"status": "responded", "score": 75}, format="json")

        self.customer.refresh_from_db()
        self.assertEqual(str(self.customer.ces_percentage), "75.00")

    def test_responding_to_an_account_survey_syncs_the_account_not_the_customer(self):
        survey = Survey.objects.create(
            account=self.account, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        self.client.patch(self._url(survey), {"status": "responded", "score": -20}, format="json")

        self.account.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(self.account.nps_score, -20)
        self.assertIsNone(self.customer.nps_score)

    def test_explicit_responded_at_is_not_overridden(self):
        survey = Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        self.client.patch(
            self._url(survey),
            {"status": "responded", "score": 50, "responded_at": "2026-08-15"},
            format="json",
        )

        survey.refresh_from_db()
        self.assertEqual(str(survey.responded_at), "2026-08-15")

    def test_cannot_change_survey_type_after_responding(self):
        survey = Survey.objects.create(
            customer=self.customer,
            survey_type=Survey.SurveyType.NPS,
            sent_at="2026-09-01",
            status=Survey.Status.RESPONDED,
            score=70,
            responded_at="2026-09-05",
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(self._url(survey), {"survey_type": "csat"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("survey_type", response.data)
        survey.refresh_from_db()
        self.assertEqual(survey.survey_type, Survey.SurveyType.NPS)

    def test_can_edit_sent_at_and_correct_a_responded_score(self):
        survey = Survey.objects.create(
            customer=self.customer,
            survey_type=Survey.SurveyType.NPS,
            sent_at="2026-09-01",
            status=Survey.Status.RESPONDED,
            score=70,
            responded_at="2026-09-05",
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            self._url(survey), {"sent_at": "2026-08-30", "score": 90}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        survey.refresh_from_db()
        self.assertEqual(str(survey.sent_at), "2026-08-30")
        self.assertEqual(survey.score, 90)
        # Correcting an already-responded score re-syncs the parent too.
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.nps_score, 90)

    def test_can_mark_a_sent_survey_expired(self):
        survey = Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(self._url(survey), {"status": "expired"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        survey.refresh_from_db()
        self.assertEqual(survey.status, Survey.Status.EXPIRED)

    def test_can_delete_a_survey(self):
        survey = Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.delete(self._url(survey))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Survey.objects.filter(pk=survey.id).exists())

    def test_another_organisations_admin_gets_404(self):
        survey = Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(other_admin)

        self.assertEqual(self.client.get(self._url(survey)).status_code, status.HTTP_404_NOT_FOUND)


class CustomerCanvasListTests(APITestCase):
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
        self.url = f"/api/v1/customers/{self.customer.id}/canvases/"

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_can_add_an_organization_level_canvas(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url, {"name": "Renewal Strategy Q3"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        canvas = Canvas.objects.get(pk=response.data["id"])
        self.assertEqual(canvas.customer, self.customer)
        self.assertIsNone(canvas.account)
        self.assertEqual(canvas.name, "Renewal Strategy Q3")

    def test_rolls_up_this_customers_own_accounts_canvases_too(self):
        account = create_account(self.customer, name="North America")
        Canvas.objects.create(customer=self.customer, name="Org Canvas")
        Canvas.objects.create(account=account, name="Account Canvas")
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        names = {row["name"] for row in response.data}
        self.assertEqual(names, {"Org Canvas", "Account Canvas"})

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/canvases/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AccountCanvasListTests(APITestCase):
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
        self.account = create_account(self.customer, name="North America")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/canvases/"

    def test_admin_can_add_an_account_level_canvas(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url, {"name": "Stakeholder Map"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        canvas = Canvas.objects.get(pk=response.data["id"])
        self.assertEqual(canvas.account, self.account)
        self.assertIsNone(canvas.customer)
        self.assertEqual(response.data["account_id"], self.account.id)


class CanvasListTests(APITestCase):
    """/api/v1/canvases/ — the one Canvas view not nested under a single
    Customer/Account (see CanvasListView's own docstring). Powers the
    standalone Canvas gallery."""

    url = "/api/v1/canvases/"

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
        self.account = create_account(self.customer, name="North America")

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_both_org_level_and_account_level_canvases_as_a_plain_array(self):
        Canvas.objects.create(customer=self.customer, name="Org Canvas")
        Canvas.objects.create(account=self.account, name="Account Canvas")
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        names = {row["name"] for row in response.data}
        self.assertEqual(names, {"Org Canvas", "Account Canvas"})

    def test_does_not_leak_another_organisations_canvases(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_customer = Customer.objects.create(organisation=other_org, name="Other Co")
        Canvas.objects.create(customer=other_customer, name="Other Org's Canvas")
        Canvas.objects.create(customer=self.customer, name="My Canvas")
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["name"], "My Canvas")

    def test_posting_with_customer_id_creates_an_organization_level_canvas(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, {"name": "New Canvas", "customer_id": self.customer.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        canvas = Canvas.objects.get(pk=response.data["id"])
        self.assertEqual(canvas.customer, self.customer)
        self.assertIsNone(canvas.account)

    def test_posting_with_account_id_creates_an_account_level_canvas(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, {"name": "New Canvas", "account_id": self.account.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        canvas = Canvas.objects.get(pk=response.data["id"])
        self.assertEqual(canvas.account, self.account)
        self.assertIsNone(canvas.customer)

    def test_posting_with_neither_customer_id_nor_account_id_is_400(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.url, {"name": "New Canvas"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CanvasDetailTests(APITestCase):
    """GET/PATCH/DELETE /api/v1/canvases/<id>/ — flat, not nested."""

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

    def _url(self, canvas):
        return f"/api/v1/canvases/{canvas.id}/"

    def test_unauthenticated_cannot_view(self):
        canvas = Canvas.objects.create(customer=self.customer)
        response = self.client.get(self._url(canvas))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_can_patch_name_nodes_and_edges(self):
        canvas = Canvas.objects.create(customer=self.customer)
        self.client.force_authenticate(self.admin)
        nodes = [
            {"id": "n1", "type": "contact", "position": {"x": 0, "y": 0}, "data": {"contact_id": 1}}
        ]
        edges = [{"id": "e1", "source": "n1", "target": "n2", "label": "Reports to"}]

        response = self.client.patch(
            self._url(canvas), {"name": "Renamed", "nodes": nodes, "edges": edges}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        canvas.refresh_from_db()
        self.assertEqual(canvas.name, "Renamed")
        self.assertEqual(canvas.nodes, nodes)
        self.assertEqual(canvas.edges, edges)

    def test_can_delete_a_canvas(self):
        canvas = Canvas.objects.create(customer=self.customer)
        self.client.force_authenticate(self.admin)

        response = self.client.delete(self._url(canvas))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Canvas.objects.filter(pk=canvas.id).exists())

    def test_another_organisations_admin_gets_404(self):
        canvas = Canvas.objects.create(customer=self.customer)
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(other_admin)

        self.assertEqual(self.client.get(self._url(canvas)).status_code, status.HTTP_404_NOT_FOUND)


class AccountListTests(APITestCase):
    """/api/v1/accounts/ — the one Account view not nested under a
    single Customer (see AccountListView's own docstring)."""

    url = "/api/v1/accounts/"

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

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_accounts_across_every_customer(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        create_account(self.customer, name="North America")
        create_account(other_customer, name="EMEA")
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        names = {row["name"] for row in response.data["results"]}
        self.assertEqual(names, {"North America", "EMEA"})

    def test_response_includes_customer_name(self):
        create_account(self.customer, name="North America")
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        row = response.data["results"][0]
        self.assertEqual(row["customers"], [{"id": self.customer.id, "name": "Globex"}])

    def test_search_matches_name(self):
        create_account(self.customer, name="North America")
        create_account(self.customer, name="EMEA")
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url, {"search": "north"})

        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "North America")

    def test_company_filter_matches_one_customers_accounts(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        create_account(self.customer, name="North America")
        create_account(other_customer, name="EMEA")
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url, {"company": self.customer.id})

        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "North America")

    def test_does_not_leak_another_organisations_accounts(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        other_customer = Customer.objects.create(organisation=other_org, name="Other Co")
        create_account(other_customer, name="Someone Else's Account")
        create_account(self.customer, name="North America")
        self.client.force_authenticate(other_admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "Someone Else's Account")


class AccountStatsTests(APITestCase):
    """GET /api/v1/accounts/stats/ — aggregate rollups for the standalone
    Accounts page's own MetricsPanel (Health/NPS/Lifecycle Stages)."""

    url = "/api/v1/accounts/stats/"

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
        create_account(self.customer, name="Good Acc", health_score="9.0", arr="12000.00")
        create_account(self.customer, name="Also Good Acc", health_score="7.0", arr="6000.00")
        create_account(self.customer, name="Average Acc", health_score="5.0", arr="2400.00")
        create_account(self.customer, name="Poor Acc", health_score="1.0", arr="1200.00")

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

    def test_buckets_by_lifecycle_stage_counting_churned_accounts_too(self):
        create_account(self.customer, name="Live Acc", lifecycle_stage="live", arr="12000.00")
        create_account(self.customer, name="Churned Acc", lifecycle_stage="churn", arr="6000.00")

        response = self.client.get(self.url)

        self.assertEqual(response.data["lifecycle"]["live"]["count"], 1)
        self.assertEqual(response.data["lifecycle"]["churn"]["count"], 1)
        self.assertEqual(response.data["lifecycle"]["churn"]["arr"], 6000.0)

    def test_nps_breakdown_excludes_unscored_accounts_from_counts_and_denominator(self):
        create_account(self.customer, name="Promoter 1", nps_score=80)
        create_account(self.customer, name="Promoter 2", nps_score=40)
        create_account(self.customer, name="Passive", nps_score=0)
        create_account(self.customer, name="Detractor", nps_score=-60)
        create_account(self.customer, name="Not Yet Scored", nps_score=None)

        response = self.client.get(self.url)
        nps = response.data["nps"]
        self.assertEqual(nps["promoters"], 2)
        self.assertEqual(nps["passives"], 1)
        self.assertEqual(nps["detractors"], 1)
        # round((2 - 1) / 4 * 100) = 25 -- the unscored account doesn't
        # count towards the denominator either.
        self.assertEqual(nps["score"], 25)

    def test_scoped_to_the_callers_organisation(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_customer = Customer.objects.create(organisation=other_org, name="Not Yours Inc")
        create_account(other_customer, name="Not Yours", health_score="9.0", arr="99999.00")
        create_account(self.customer, name="Mine", health_score="9.0", arr="100.00")

        response = self.client.get(self.url)
        good = response.data["health"]["good"]
        self.assertEqual(good["count"], 1)
        self.assertEqual(good["arr"], 100.0)


class TaskListTests(APITestCase):
    """/api/v1/tasks/ — the one Task view not nested under a single
    Customer/Account (see TaskListView's own docstring). Powers
    Cockpit's own "My Tasks" panel."""

    url = "/api/v1/tasks/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.owner = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        self.other_user = User.objects.create_user(
            email="dana@acme.io", password="supersecret1", name="Dana", organisation=self.org
        )
        self.customer = Customer.objects.create(
            organisation=self.org, name="Globex", owner=self.owner
        )
        self.account = create_account(self.customer, name="North America", owner=self.other_user)

    def _task_kwargs(self, **overrides):
        kwargs = {
            "title": "Prepare QBR deck",
            "assignee_name": "Edgar Holmes",
            "due_date": "2026-03-15",
            "priority": Task.Priority.HIGH,
            "status": Task.Status.IN_PROGRESS,
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_both_customer_and_account_level_tasks(self):
        Task.objects.create(customer=self.customer, **self._task_kwargs(title="Org Task"))
        Task.objects.create(account=self.account, **self._task_kwargs(title="Account Task"))
        self.client.force_authenticate(self.owner)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = {row["title"] for row in response.data}
        self.assertEqual(titles, {"Org Task", "Account Task"})

    def test_exposes_parent_name_and_type(self):
        Task.objects.create(customer=self.customer, **self._task_kwargs(title="Org Task"))
        Task.objects.create(account=self.account, **self._task_kwargs(title="Account Task"))
        self.client.force_authenticate(self.owner)

        response = self.client.get(self.url)

        by_title = {row["title"]: row for row in response.data}
        self.assertEqual(by_title["Org Task"]["parent_name"], "Globex")
        self.assertEqual(by_title["Org Task"]["parent_type"], "customer")
        self.assertEqual(by_title["Account Task"]["parent_name"], "North America")
        self.assertEqual(by_title["Account Task"]["parent_type"], "account")
        self.assertEqual(by_title["Org Task"]["priority_display"], "High")
        self.assertEqual(by_title["Org Task"]["status_display"], "In Progress")

    def test_does_not_leak_another_organisations_tasks(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_org_user = User.objects.create_user(
            email="other@other.io", password="supersecret1", name="Other", organisation=other_org
        )
        Task.objects.create(customer=self.customer, **self._task_kwargs())
        self.client.force_authenticate(other_org_user)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_mine_filters_to_the_callers_own_owned_customers_and_accounts(self):
        Task.objects.create(customer=self.customer, **self._task_kwargs(title="Carl's Org Task"))
        Task.objects.create(account=self.account, **self._task_kwargs(title="Dana's Account Task"))
        self.client.force_authenticate(self.owner)

        response = self.client.get(self.url, {"mine": "true"})

        titles = {row["title"] for row in response.data}
        self.assertEqual(titles, {"Carl's Org Task"})

    def test_without_mine_returns_every_task_in_the_organisation(self):
        Task.objects.create(customer=self.customer, **self._task_kwargs(title="Carl's Org Task"))
        Task.objects.create(account=self.account, **self._task_kwargs(title="Dana's Account Task"))
        self.client.force_authenticate(self.owner)

        response = self.client.get(self.url)

        titles = {row["title"] for row in response.data}
        self.assertEqual(titles, {"Carl's Org Task", "Dana's Account Task"})


class CockpitSummaryTests(APITestCase):
    """GET /api/v1/cockpit/summary/ — real numbers for Cockpit's own
    "My Portfolio Summary"/"Renewals" tiles, scoped to the caller's own
    owned book of business."""

    url = "/api/v1/cockpit/summary/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.owner = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        self.other_user = User.objects.create_user(
            email="dana@acme.io", password="supersecret1", name="Dana", organisation=self.org
        )
        self.client.force_authenticate(self.owner)

    def test_unauthenticated_cannot_access(self):
        self.client.force_authenticate(None)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_empty_book_returns_zeroed_summary_not_an_error(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["customers"]["count"], 0)
        self.assertEqual(response.data["customers"]["value"], 0)
        self.assertEqual(response.data["accounts"]["count"], 0)
        self.assertEqual(response.data["renewals_next_30_days"]["customers"]["count"], 0)

    def test_only_counts_customers_and_accounts_the_caller_owns(self):
        Customer.objects.create(
            organisation=self.org, name="Mine", owner=self.owner, arr_billed_at_hq="1000.00"
        )
        Customer.objects.create(
            organisation=self.org,
            name="Not Mine",
            owner=self.other_user,
            arr_billed_at_hq="9999.00",
        )
        # An Account's own ownership is independent of its parent
        # Customer's — "my account" under someone else's own customer
        # still counts towards my own book, same as CockpitSummaryView's
        # own separate owner= filters on each queryset.
        other_customer = Customer.objects.create(
            organisation=self.org, name="Parent", owner=self.other_user
        )
        create_account(other_customer, name="My Account", owner=self.owner, arr="500.00")

        response = self.client.get(self.url)

        self.assertEqual(response.data["customers"]["count"], 1)
        self.assertEqual(response.data["customers"]["value"], 1000.0)
        self.assertEqual(response.data["accounts"]["count"], 1)
        self.assertEqual(response.data["accounts"]["value"], 500.0)

    def test_health_distribution_reflects_real_health_scores(self):
        Customer.objects.create(
            organisation=self.org, name="Good", owner=self.owner, health_score="9.0"
        )
        Customer.objects.create(
            organisation=self.org, name="Poor", owner=self.owner, health_score="1.0"
        )

        response = self.client.get(self.url)

        self.assertEqual(response.data["customers"]["health"]["good"], 1)
        self.assertEqual(response.data["customers"]["health"]["poor"], 1)

    def test_renewals_next_30_days_includes_only_customers_renewing_soon(self):
        today = timezone.localdate()
        Customer.objects.create(
            organisation=self.org,
            name="Renewing Soon",
            owner=self.owner,
            arr_billed_at_hq="1200.00",
            renewal_date=today + timedelta(days=10),
        )
        Customer.objects.create(
            organisation=self.org,
            name="Renewing Later",
            owner=self.owner,
            arr_billed_at_hq="2400.00",
            renewal_date=today + timedelta(days=90),
        )
        Customer.objects.create(
            organisation=self.org,
            name="No Renewal Date",
            owner=self.owner,
            arr_billed_at_hq="500.00",
        )

        response = self.client.get(self.url)

        renewals = response.data["renewals_next_30_days"]["customers"]
        self.assertEqual(renewals["count"], 1)
        self.assertEqual(renewals["value"], 1200.0)

    def test_renewals_excludes_already_churned_customers(self):
        today = timezone.localdate()
        Customer.objects.create(
            organisation=self.org,
            name="Churned",
            owner=self.owner,
            lifecycle_stage=Customer.LifecycleStage.CHURN,
            renewal_date=today + timedelta(days=5),
        )

        response = self.client.get(self.url)

        self.assertEqual(response.data["renewals_next_30_days"]["customers"]["count"], 0)

    def test_converts_customer_arr_to_org_currency(self):
        from services.fx_rates.models import FxRate

        FxRate.objects.create(organisation=self.org, currency="EUR", rate_to_org_currency="2.0")
        Customer.objects.create(
            organisation=self.org,
            name="Euro Customer",
            owner=self.owner,
            currency="EUR",
            arr_billed_at_hq="1000.00",
        )

        response = self.client.get(self.url)

        self.assertEqual(response.data["customers"]["value"], 2000.0)
        self.assertEqual(response.data["customers"]["unconverted_count"], 0)

    def test_unconvertible_currency_excluded_from_value_but_still_counted(self):
        Customer.objects.create(
            organisation=self.org,
            name="No Rate Configured",
            owner=self.owner,
            currency="GBP",
            arr_billed_at_hq="1000.00",
        )

        response = self.client.get(self.url)

        self.assertEqual(response.data["customers"]["count"], 1)
        self.assertEqual(response.data["customers"]["value"], 0)
        self.assertEqual(response.data["customers"]["unconverted_count"], 1)
