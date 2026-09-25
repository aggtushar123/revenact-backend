"""Integration tier: through the real URLconf + real test DB."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.connectors.models import Connector
from services.customers.models import (
    Account,
    Activity,
    CalendarEvent,
    Canvas,
    Contact,
    Customer,
    Email,
    Headline,
    HealthSnapshot,
    Note,
    Opportunity,
    Product,
    Risk,
    Survey,
    Task,
    Ticket,
)
from services.notifications.models import Notification


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
        self.product = Product.objects.create(organisation=self.org, name="Product A")
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

    def test_creating_with_a_real_owner_sends_them_a_real_notification(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url, {"name": "Initech", "owner_id": self.csm.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        notification = Notification.objects.get(recipient=self.csm)
        self.assertEqual(notification.kind, Notification.Kind.CUSTOMER_ASSIGNED)
        self.assertIn("Initech", notification.message)

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
                # An id now, not a name: products are the tenant's own rows
                # (see Product's own docstring).
                "primary_product": self.product.id,
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


class CustomerIdsFilterTests(APITestCase):
    url = "/api/v1/customers/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.a = Customer.objects.create(organisation=self.org, name="A", owner=self.csm)
        self.b = Customer.objects.create(organisation=self.org, name="B", owner=self.csm)
        self.c = Customer.objects.create(organisation=self.org, name="C", owner=self.csm)
        self.theirs = Customer.objects.create(organisation=self.org, name="T", owner=self.other)
        self.client.force_authenticate(self.csm)

    def names(self, params):
        return sorted(row["name"] for row in self.client.get(self.url, params).json()["results"])

    def test_only_the_named_customers(self):
        self.assertEqual(self.names({"ids": f"{self.a.pk},{self.c.pk}"}), ["A", "C"])

    def test_scoping_still_applies(self):
        self.assertEqual(self.names({"ids": f"{self.a.pk},{self.theirs.pk}"}), ["A"])

    def test_bad_parts_are_ignored_when_one_is_good(self):
        self.assertEqual(self.names({"ids": f"x,{self.b.pk},"}), ["B"])

    def test_all_bad_or_empty_ids_means_nothing(self):
        self.assertEqual(self.names({"ids": "x,y"}), [])
        self.assertEqual(self.names({"ids": ""}), [])

    def test_a_named_archived_customer_is_returned(self):
        archived = Customer.objects.create(
            organisation=self.org, name="Gone", owner=self.csm, is_archived=True
        )
        self.assertEqual(self.names({"ids": f"{self.a.pk},{archived.pk}"}), ["A", "Gone"])
        self.assertEqual(self.names({}), ["A", "B", "C"])


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

        notification = Notification.objects.get(recipient=self.csm)
        self.assertEqual(notification.kind, Notification.Kind.CUSTOMER_ASSIGNED)
        self.assertEqual(notification.actor, self.admin)
        self.assertIn("Globex", notification.message)
        self.assertEqual(notification.link, f"/organizations/{self.customer.id}")

    def test_reassigning_to_the_same_owner_sends_no_duplicate_notification(self):
        self.customer.owner = self.csm
        self.customer.save()
        self.client.force_authenticate(self.admin)

        self.client.patch(self.url, {"owner_id": self.csm.id}, format="json")

        self.assertEqual(Notification.objects.filter(recipient=self.csm).count(), 0)

    def test_assigning_a_customer_to_yourself_sends_no_notification(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(self.url, {"owner_id": self.admin.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.filter(recipient=self.admin).count(), 0)

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
        # Self-assignment — the admin owning their own new account — sends
        # no notification (see _notify_owner_assigned's own docstring).
        self.assertEqual(Notification.objects.filter(recipient=self.admin).count(), 0)

    def test_creating_an_account_with_a_real_owner_sends_them_a_real_notification(self):
        csm = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, {"name": "North America", "owner_id": csm.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        notification = Notification.objects.get(recipient=csm)
        self.assertEqual(notification.kind, Notification.Kind.ACCOUNT_ASSIGNED)
        self.assertEqual(notification.actor, self.admin)
        self.assertIn("North America", notification.message)
        account = Account.objects.get(name="North America")
        self.assertEqual(notification.link, f"/accounts/{account.id}")

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

    def test_an_accounts_owner_is_gated_and_handed_over_on_the_record_like_an_organisations(self):
        from services.accounts.capabilities import Capability
        from services.accounts.models import Role
        from services.knowledge.models import Contribution

        # People who can see every account but cannot manage org settings.
        lead = Role.objects.create(
            organisation=self.org,
            name="Lead",
            slug="lead",
            permissions=[Capability.VIEW_ALL_ACCOUNTS],
        )
        carl = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        dana = User.objects.create_user(
            email="dana@acme.io", password="supersecret1", name="Dana", organisation=self.org
        )
        User.objects.filter(pk__in=[carl.pk, dana.pk]).update(role=lead)
        carl.refresh_from_db()
        dana.refresh_from_db()

        # Unowned: anyone may claim it.
        self.client.force_authenticate(dana)
        response = self.client.patch(self.url, {"owner_id": carl.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Owned by Carl: Dana — not the owner, not above him, no settings
        # capability — may not move it.
        refused = self.client.patch(self.url, {"owner_id": dana.id}, format="json")
        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Only the current account owner", str(refused.data["owner_id"]))

        # Carl hands it over with a note: written down on every organisation
        # the account belongs to, and Dana is told.
        initech = Customer.objects.create(organisation=self.org, name="Initech")
        self.account.customers.add(initech)
        self.client.force_authenticate(carl)
        handed = self.client.patch(
            self.url,
            {"owner_id": dana.id, "handover_note": "Moving to Dana's region."},
            format="json",
        )
        self.assertEqual(handed.status_code, status.HTTP_200_OK)
        self.assertEqual(handed.data["owner"]["name"], "Dana")
        notes = Contribution.objects.filter(
            body="Owner of account North America changed from Carl to Dana. "
            "Moving to Dana's region."
        )
        self.assertEqual(sorted(c.customer.name for c in notes), ["Globex", "Initech"])
        self.assertEqual({c.author for c in notes}, {carl})
        self.assertTrue(
            Notification.objects.filter(
                recipient=dana, kind=Notification.Kind.ACCOUNT_ASSIGNED
            ).exists()
        )

        # An org-settings manager may always reassign; clearing is recorded too.
        self.client.force_authenticate(self.admin)
        cleared = self.client.patch(self.url, {"owner_id": None}, format="json")
        self.assertEqual(cleared.status_code, status.HTTP_200_OK)
        self.assertTrue(
            Contribution.objects.filter(
                body="Owner of account North America changed from Dana to nobody."
            ).exists()
        )

    def test_assigning_a_real_new_owner_sends_them_a_real_notification(self):
        csm = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(self.url, {"owner_id": csm.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notification = Notification.objects.get(recipient=csm)
        self.assertEqual(notification.kind, Notification.Kind.ACCOUNT_ASSIGNED)
        self.assertEqual(notification.actor, self.admin)
        self.assertIn("North America", notification.message)
        self.assertEqual(notification.link, f"/accounts/{self.account.id}")

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
        self.assertEqual(response.data["renewals"]["customers"]["count"], 0)
        self.assertEqual(response.data["renewals"]["items"], [])
        self.assertEqual(response.data["renewals"]["window_days"], 30)

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

    def test_renewals_includes_only_customers_renewing_within_the_window(self):
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

        renewals = response.data["renewals"]["customers"]
        self.assertEqual(renewals["count"], 1)
        self.assertEqual(renewals["value"], 1200.0)
        self.assertEqual(len(response.data["renewals"]["items"]), 1)
        self.assertEqual(response.data["renewals"]["items"][0]["name"], "Renewing Soon")

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

        self.assertEqual(response.data["renewals"]["customers"]["count"], 0)

    def test_days_query_param_controls_the_renewal_window(self):
        today = timezone.localdate()
        Customer.objects.create(
            organisation=self.org,
            name="Renewing in 45 days",
            owner=self.owner,
            arr_billed_at_hq="1000.00",
            renewal_date=today + timedelta(days=45),
        )

        response_30 = self.client.get(self.url, {"days": "30"})
        self.assertEqual(response_30.data["renewals"]["customers"]["count"], 0)
        self.assertEqual(response_30.data["renewals"]["window_days"], 30)

        response_60 = self.client.get(self.url, {"days": "60"})
        self.assertEqual(response_60.data["renewals"]["customers"]["count"], 1)
        self.assertEqual(response_60.data["renewals"]["window_days"], 60)

    def test_invalid_days_falls_back_to_30_instead_of_erroring(self):
        response = self.client.get(self.url, {"days": "not-a-number"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["renewals"]["window_days"], 30)

    def test_renewal_items_merge_customers_and_accounts_sorted_soonest_first(self):
        today = timezone.localdate()
        Customer.objects.create(
            organisation=self.org,
            name="Later Customer",
            owner=self.owner,
            arr_billed_at_hq="1000.00",
            renewal_date=today + timedelta(days=20),
        )
        account_parent = Customer.objects.create(
            organisation=self.org, name="Account Parent", owner=self.owner
        )
        create_account(
            account_parent,
            name="Sooner Account",
            owner=self.owner,
            arr="500.00",
            renewal_date=today + timedelta(days=5),
        )

        response = self.client.get(self.url)

        items = response.data["renewals"]["items"]
        self.assertEqual([item["name"] for item in items], ["Sooner Account", "Later Customer"])
        self.assertEqual(items[0]["type"], "account")
        self.assertEqual(items[1]["type"], "customer")

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


class CustomerHeadlineListTests(APITestCase):
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
        self.url = f"/api/v1/customers/{self.customer.id}/headlines/"

    def _headline_kwargs(self, **overrides):
        kwargs = {
            "title": "Globex Renewal and Expansion",
            "content": "Renewal positioned for success with growth opportunities.",
            "status": Headline.Status.OPEN,
            "period_start": "2025-11-20",
            "period_end": "2026-01-21",
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_customers_headlines_as_a_plain_array(self):
        Headline.objects.create(customer=self.customer, **self._headline_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(response.data[0]["title"], "Globex Renewal and Expansion")
        self.assertEqual(response.data[0]["status_display"], "Open")

    def test_group_is_derived_from_period_end(self):
        """The mock stored a free-text 'January' group; the card's pill
        now comes from the real date, same as every other tab's own
        date-group header."""
        Headline.objects.create(customer=self.customer, **self._headline_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data[0]["group"], "January 2026")

    def test_a_summary_has_no_group_pill(self):
        Headline.objects.create(
            customer=self.customer,
            **self._headline_kwargs(kind=Headline.Kind.SUMMARY, status=""),
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data[0]["group"], "")

    def test_data_sources_render_as_the_cards_prose_list(self):
        Headline.objects.create(
            customer=self.customer,
            **self._headline_kwargs(
                kind=Headline.Kind.SUMMARY,
                status="",
                data_sources=["notes", "emails", "call_transcripts", "tickets"],
            ),
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(
            response.data[0]["data_sources_display"],
            "Notes, Emails, Call Transcripts and Tickets",
        )

    def test_no_data_sources_renders_an_empty_footer_line(self):
        Headline.objects.create(customer=self.customer, **self._headline_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data[0]["data_sources_display"], "")

    def test_does_not_include_an_accounts_headlines(self):
        account = create_account(self.customer, name="North America")
        Headline.objects.create(account=account, **self._headline_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_customers_headlines(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        Headline.objects.create(customer=other_customer, **self._headline_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_nonexistent_customer_id_is_404(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/999999/headlines/")
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

    def test_writes_a_headline_by_hand(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url, self._headline_kwargs(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Headline.objects.get().customer, self.customer)
        self.assertIsNone(response.data["generated_at"])

    def test_rejects_an_unknown_data_source(self):
        """data_sources is a JSONField, so nothing but this validator
        stops the card claiming a source that was never read."""
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, self._headline_kwargs(data_sources=["notes", "tea_leaves"]), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("tea_leaves", str(response.data["data_sources"]))

    def test_rejects_a_summary_with_a_status(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, self._headline_kwargs(kind=Headline.Kind.SUMMARY), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("no status", str(response.data["status"]))

    def test_rejects_a_period_that_ends_before_it_starts(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            self._headline_kwargs(period_start="2026-05-01", period_end="2026-01-01"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AccountHeadlineListTests(APITestCase):
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
        self.account = create_account(self.customer, name="Globex EMEA")
        self.url = f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/headlines/"

    def _headline_kwargs(self, **overrides):
        kwargs = {
            "title": "Globex EMEA Renewal",
            "content": "Strong renewal momentum with 96% utilization.",
            "status": Headline.Status.OPEN,
            "period_end": "2026-01-21",
        }
        kwargs.update(overrides)
        return kwargs

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_accounts_headlines(self):
        Headline.objects.create(account=self.account, **self._headline_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["title"], "Globex EMEA Renewal")

    def test_does_not_include_the_customers_own_headlines(self):
        Headline.objects.create(customer=self.customer, **self._headline_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_does_not_leak_another_accounts_headlines(self):
        """The defect the mock had: every account rendered the same
        hardcoded Apple headlines, because nothing scoped them."""
        other_account = create_account(self.customer, name="Globex APAC")
        Headline.objects.create(account=other_account, **self._headline_kwargs())
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_wrong_customer_id_in_the_url_is_404_even_for_a_valid_account_id(self):
        other_customer = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.admin)

        response = self.client.get(
            f"/api/v1/customers/{other_customer.id}/accounts/{self.account.id}/headlines/"
        )
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

    def test_writes_an_account_headline_by_hand(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url, self._headline_kwargs(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Headline.objects.get().account, self.account)


class HeadlineDetailTests(APITestCase):
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
        self.headline = Headline.objects.create(
            customer=self.customer,
            title="Globex Renewal",
            content="Original wording.",
            status=Headline.Status.OPEN,
        )
        self.url = f"/api/v1/headlines/{self.headline.id}/"

    def test_unauthenticated_cannot_read(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_corrects_a_headline(self):
        self.client.force_authenticate(self.admin)

        response = self.client.patch(self.url, {"content": "Corrected."}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.headline.refresh_from_db()
        self.assertEqual(self.headline.content, "Corrected.")

    def test_deletes_a_headline(self):
        self.client.force_authenticate(self.admin)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Headline.objects.exists())

    def test_another_organisation_cannot_reach_it(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(other_admin)

        response = self.client.patch(self.url, {"content": "Nope."}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_reaches_an_account_level_headline_through_the_m2m(self):
        """The queryset scopes account-level cards through
        account__customers__organisation — a different path from the
        customer-level one, so it gets its own test."""
        account = create_account(self.customer, name="Globex EMEA")
        headline = Headline.objects.create(account=account, title="T", content="C")
        self.client.force_authenticate(self.admin)

        response = self.client.get(f"/api/v1/headlines/{headline.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class HeadlineGenerateTests(APITestCase):
    """The one external call is always patched — a real one would be
    paid, slow, and non-deterministic. Same approach as
    SendMessageView's own tests."""

    MODEL_REPLY = """{
      "summary": {
        "title": "TL;DR (Last 90 days)",
        "content": "Account health is strong with renewal momentum."
      },
      "headlines": [
        {
          "title": "Globex EMEA Renewal and Expansion",
          "content": "Renewal coordinated with Priya and Leo.",
          "status": "open",
          "period_start": "2025-11-20",
          "period_end": "2026-01-21"
        }
      ]
    }"""

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
        self.account = create_account(self.customer, name="Globex EMEA")
        self.url = (
            f"/api/v1/customers/{self.customer.id}/accounts/{self.account.id}/headlines/generate/"
        )
        Note.objects.create(
            account=self.account,
            title="Commercial Negotiation Summary",
            author_name="Edgar Holmes",
            body="Customer requested 15% discount for a 3-year commitment.",
            logged_at=timezone.now().date(),
        )

    def test_unauthenticated_cannot_generate(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("services.customers.headline_generation.get_completion")
    def test_writes_the_models_cards_against_the_accounts_real_records(self, get_completion):
        get_completion.return_value = self.MODEL_REPLY
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Headline.objects.count(), 2)
        summary = Headline.objects.get(kind=Headline.Kind.SUMMARY)
        self.assertEqual(summary.account, self.account)
        self.assertIsNotNone(summary.generated_at)

    @patch("services.customers.headline_generation.get_completion")
    def test_data_sources_name_only_what_was_actually_read(self, get_completion):
        """The account has notes and nothing else — the card must not
        claim emails, tickets or calls it never saw. That claim being
        decorative is exactly what this replaces."""
        get_completion.return_value = self.MODEL_REPLY
        self.client.force_authenticate(self.admin)

        self.client.post(self.url)

        summary = Headline.objects.get(kind=Headline.Kind.SUMMARY)
        self.assertEqual(summary.data_sources, ["notes"])

    @patch("services.customers.headline_generation.get_completion")
    def test_the_prompt_carries_the_accounts_own_records(self, get_completion):
        get_completion.return_value = self.MODEL_REPLY
        self.client.force_authenticate(self.admin)

        self.client.post(self.url)

        prompt = get_completion.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Commercial Negotiation Summary", prompt)
        self.assertIn("Globex EMEA", prompt)

    @patch("services.customers.headline_generation.get_completion")
    def test_regenerating_replaces_its_own_previous_cards(self, get_completion):
        get_completion.return_value = self.MODEL_REPLY
        self.client.force_authenticate(self.admin)

        self.client.post(self.url)
        first_ids = set(Headline.objects.values_list("id", flat=True))
        self.client.post(self.url)

        self.assertEqual(Headline.objects.count(), 2)
        self.assertFalse(first_ids & set(Headline.objects.values_list("id", flat=True)))

    @patch("services.customers.headline_generation.get_completion")
    def test_regenerating_never_touches_a_hand_written_card(self, get_completion):
        """A CSM's own correction has to survive a regenerate, or the
        write endpoints would be a trap."""
        get_completion.return_value = self.MODEL_REPLY
        hand_written = Headline.objects.create(
            account=self.account, title="Written by a human", content="Keep me."
        )
        self.client.force_authenticate(self.admin)

        self.client.post(self.url)
        self.client.post(self.url)

        hand_written.refresh_from_db()
        self.assertEqual(hand_written.title, "Written by a human")

    @patch("services.customers.headline_generation.get_completion")
    def test_an_account_with_nothing_to_summarise_is_422_and_makes_no_call(self, get_completion):
        empty_account = create_account(self.customer, name="Globex APAC")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/v1/customers/{self.customer.id}/accounts/{empty_account.id}/headlines/generate/"
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("nothing to summarise", response.data["detail"])
        get_completion.assert_not_called()

    @patch("services.customers.headline_generation.get_completion")
    def test_unreadable_model_output_is_502_and_keeps_the_old_cards(self, get_completion):
        get_completion.return_value = "I'm afraid I can't do that."
        existing = Headline.objects.create(
            account=self.account, title="Still here", content="C", generated_at=timezone.now()
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertTrue(Headline.objects.filter(pk=existing.pk).exists())

    @patch("services.customers.headline_generation.get_completion")
    def test_a_fenced_json_reply_is_still_read(self, get_completion):
        get_completion.return_value = f"```json\n{self.MODEL_REPLY}\n```"
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Headline.objects.count(), 2)

    @patch("services.customers.headline_generation.get_completion")
    def test_an_unconfigured_provider_is_503(self, get_completion):
        from services.copilot.anthropic_client import CopilotNotConfigured

        get_completion.side_effect = CopilotNotConfigured("Set ANTHROPIC_API_KEY in your .env.")
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("ANTHROPIC_API_KEY", response.data["detail"])

    @patch("services.customers.headline_generation.get_completion")
    def test_a_failed_call_is_502(self, get_completion):
        from services.copilot.anthropic_client import CopilotRequestFailed

        get_completion.side_effect = CopilotRequestFailed("rate limited")
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch("services.customers.headline_generation.get_completion")
    def test_a_customer_level_generate_attaches_to_the_customer(self, get_completion):
        get_completion.return_value = self.MODEL_REPLY
        Note.objects.create(
            customer=self.customer,
            title="Renewal Strategy Discussion",
            author_name="Natalie Reyes",
            body="Multi-year deal preferred.",
            logged_at=timezone.now().date(),
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post(f"/api/v1/customers/{self.customer.id}/headlines/generate/")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Headline.objects.filter(customer=self.customer).count(), 2)

    @patch("services.customers.headline_generation.get_completion")
    def test_another_organisation_cannot_generate_for_this_account(self, get_completion):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(other_admin)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        get_completion.assert_not_called()

    @patch("services.customers.headline_generation.get_completion")
    def test_a_nonsense_window_is_400_and_makes_no_call(self, get_completion):
        self.client.force_authenticate(self.admin)

        response = self.client.post(self.url, {"window_days": "last tuesday"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        get_completion.assert_not_called()

    @patch("services.customers.headline_generation.get_completion")
    def test_the_callers_own_period_wording_is_what_the_model_is_given(self, get_completion):
        """The system prompt asks for a title of the form
        "TL;DR (<window>)", so the prompt has to carry the caller's
        wording rather than the raw day count — otherwise a request for
        "Last 13 months" comes back titled "Last 400 days" sitting above
        a footer that reads "Last 13 months". Caught in a real
        generation against Bedrock, not in a stubbed test."""
        get_completion.return_value = self.MODEL_REPLY
        self.client.force_authenticate(self.admin)

        self.client.post(
            self.url,
            {"window_days": 400, "time_period_label": "Last 13 months"},
            format="json",
        )

        prompt = get_completion.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Last 13 months", prompt)
        self.assertNotIn("400 days", prompt)

    @patch("services.customers.headline_generation.get_completion")
    def test_the_default_period_wording_falls_back_to_the_day_count(self, get_completion):
        get_completion.return_value = self.MODEL_REPLY
        self.client.force_authenticate(self.admin)

        self.client.post(self.url)

        prompt = get_completion.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Last 90 days", prompt)

    @patch("services.customers.headline_generation.get_completion")
    def test_a_narrow_window_excludes_older_records(self, get_completion):
        get_completion.return_value = self.MODEL_REPLY
        Note.objects.create(
            account=self.account,
            title="Ancient history",
            author_name="Edgar Holmes",
            body="Long ago.",
            logged_at=timezone.now().date() - timedelta(days=200),
        )
        self.client.force_authenticate(self.admin)

        self.client.post(self.url, {"window_days": 7}, format="json")

        prompt = get_completion.call_args.kwargs["messages"][0]["content"]
        self.assertNotIn("Ancient history", prompt)


class OwnershipVisibilityTests(APITestCase):
    """One test per endpoint *family*, not per endpoint.

    The rule itself is covered in test_scoping.py; what these check is
    that each shape of endpoint actually routes through it — nested
    list, flat list, flat detail, stats, and the body-supplied parent
    pick. A failure here means an endpoint forgot to apply the policy,
    rather than the policy being wrong.

    Every fixture sets an explicit owner. That matters: unowned records
    stay visible to everyone by design, so a test that omits `owner=`
    proves nothing about gating.
    """

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.mine = Customer.objects.create(
            organisation=self.org, name="Mine", owner=self.csm, arr_billed_at_account="100.00"
        )
        self.theirs = Customer.objects.create(
            organisation=self.org, name="Theirs", owner=self.other, arr_billed_at_account="900.00"
        )
        self.their_account = create_account(
            self.theirs, name="Theirs EMEA", owner=self.other, arr="900.00"
        )
        self.client.force_authenticate(self.csm)

    # ── flat lists ───────────────────────────────────────────────────

    def test_the_customer_list_shows_only_what_you_can_see(self):
        response = self.client.get("/api/v1/customers/")
        self.assertEqual([row["name"] for row in response.data["results"]], ["Mine"])

    def test_the_account_list_shows_only_what_you_can_see(self):
        create_account(self.mine, name="Mine EMEA", owner=self.csm)
        response = self.client.get("/api/v1/accounts/")
        self.assertEqual([row["name"] for row in response.data["results"]], ["Mine EMEA"])

    def test_a_capability_holder_still_sees_everything(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/")
        self.assertEqual(
            sorted(row["name"] for row in response.data["results"]), ["Mine", "Theirs"]
        )

    # ── flat detail ──────────────────────────────────────────────────

    def test_someone_elses_customer_is_a_404_not_a_403(self):
        """404 rather than 403 on purpose — a 403 would confirm the
        record exists to someone who can't see it."""
        response = self.client.get(f"/api/v1/customers/{self.theirs.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_you_cannot_edit_someone_elses_customer(self):
        response = self.client.patch(
            f"/api/v1/customers/{self.theirs.id}/", {"name": "Renamed"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.name, "Theirs")

    # ── nested lists (the 26-endpoint family, via its two seams) ─────

    def test_a_nested_list_under_someone_elses_customer_is_a_404(self):
        Note.objects.create(
            customer=self.theirs,
            title="Commercial Negotiation Summary",
            author_name="Edgar Holmes",
            body="Customer requested 15% discount.",
            logged_at="2026-03-15",
        )
        response = self.client.get(f"/api/v1/customers/{self.theirs.id}/notes/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_nested_list_under_someone_elses_account_is_a_404(self):
        response = self.client.get(
            f"/api/v1/customers/{self.theirs.id}/accounts/{self.their_account.id}/emails/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_your_own_nested_list_still_works(self):
        """The over-application guard: gating must not empty the pages
        of the person who does own the record."""
        Note.objects.create(
            customer=self.mine,
            title="Mine",
            author_name="Carl",
            body="B",
            logged_at="2026-03-15",
        )
        response = self.client.get(f"/api/v1/customers/{self.mine.id}/notes/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_owning_only_the_account_still_opens_its_nested_lists(self):
        """The upward reach, end to end: the parent Customer belongs to
        someone else, and the nested URL goes through it."""
        my_account = create_account(self.theirs, name="Mine EMEA", owner=self.csm)
        Note.objects.create(
            account=my_account, title="Mine", author_name="Carl", body="B", logged_at="2026-03-15"
        )

        response = self.client.get(
            f"/api/v1/customers/{self.theirs.id}/accounts/{my_account.id}/notes/"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    # ── child rollups on flat lists ──────────────────────────────────

    def test_a_flat_child_list_excludes_other_peoples_records(self):
        Task.objects.create(
            customer=self.mine, title="Mine", assignee_name="Carl", due_date="2026-04-01"
        )
        Task.objects.create(
            customer=self.theirs, title="Theirs", assignee_name="Dana", due_date="2026-04-01"
        )

        # /api/v1/tasks/ is unpaginated — a plain array, see
        # TaskListView's own docstring.
        response = self.client.get("/api/v1/tasks/")

        self.assertEqual([row["title"] for row in response.data], ["Mine"])

    # ── body-supplied parent picks ───────────────────────────────────

    def test_you_cannot_attach_a_new_record_to_a_customer_you_cannot_see(self):
        response = self.client.post(
            "/api/v1/opportunities/",
            {
                "customer_id": self.theirs.id,
                "title": "Sneaky",
                "mrr": "1000.00",
                "stage": "discovery",
                "priority": "high",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── stats ────────────────────────────────────────────────────────

    def test_stats_count_only_what_you_can_see(self):
        """A total computed from records you can't open is still a
        leak — 900 of the 1000 here belongs to someone else."""
        response = self.client.get("/api/v1/customers/stats/")

        totals = sum(bucket["arr"] for bucket in response.data["health"].values())
        self.assertEqual(totals, 100.0)

    def test_stats_are_org_wide_for_a_capability_holder(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/customers/stats/")

        totals = sum(bucket["arr"] for bucket in response.data["health"].values())
        self.assertEqual(totals, 1000.0)

    # ── unowned stays reachable ──────────────────────────────────────

    def test_an_unowned_customer_is_still_listed(self):
        Customer.objects.create(organisation=self.org, name="Unassigned")
        response = self.client.get("/api/v1/customers/")
        self.assertIn("Unassigned", [row["name"] for row in response.data["results"]])

    # ── create-time ownership ────────────────────────────────────────

    def test_creating_a_customer_makes_you_its_owner(self):
        response = self.client.post("/api/v1/customers/", {"name": "Initech"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Customer.objects.get(name="Initech").owner, self.csm)

    def test_creating_a_customer_still_honours_an_explicit_owner(self):
        response = self.client.post(
            "/api/v1/customers/", {"name": "Initech", "owner_id": self.other.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Customer.objects.get(name="Initech").owner, self.other)

    def test_creating_an_account_makes_you_its_owner(self):
        response = self.client.post(
            f"/api/v1/customers/{self.mine.id}/accounts/", {"name": "Mine EMEA"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Account.objects.get(name="Mine EMEA").owner, self.csm)


class TicketStatsTests(APITestCase):
    """Every bucket is asserted against a known fixture rather than
    "some number came back" — an aggregation that silently groups
    wrongly still returns 200 with plausible-looking rows."""

    url = "/api/v1/tickets/stats/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.mine = Customer.objects.create(organisation=self.org, name="Mine", owner=self.csm)
        self.theirs = Customer.objects.create(
            organisation=self.org, name="Theirs", owner=self.other
        )
        self.zendesk = Connector.objects.create(
            organisation=self.org, provider=Connector.Provider.ZENDESK, name="Zendesk"
        )
        self.client.force_authenticate(self.csm)

    def _ticket(self, n, **overrides):
        return Ticket.objects.create(
            **{
                "customer": self.mine,
                "ticket_number": f"TKT-{n}",
                "title": "Something broke",
                "assignee_name": "Support Team",
                "status": Ticket.Status.OPEN,
                "priority": Ticket.Priority.HIGH,
                "opened_at": "2026-03-01",
                **overrides,
            }
        )

    def test_unauthenticated_is_rejected(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    # ── the six aggregations ─────────────────────────────────────────

    def test_priority_and_status_buckets_sum_to_the_total(self):
        """The guard against Django folding Ticket.Meta.ordering into
        the GROUP BY, which makes every bucket count exactly 1 while
        still returning a well-formed 200."""
        for n in range(5):
            self._ticket(n, priority=Ticket.Priority.LOW)
        self._ticket(99, priority=Ticket.Priority.CRITICAL)

        data = self.client.get(self.url).data

        by_priority = {r["name"]: r["value"] for r in data["priority"]}
        self.assertEqual(by_priority["Low"], 5)
        self.assertEqual(by_priority["Critical"], 1)
        self.assertEqual(sum(r["value"] for r in data["priority"]), data["kpis"]["total"])
        self.assertEqual(sum(r["value"] for r in data["status"]), data["kpis"]["total"])

    def test_every_bucket_is_present_even_when_empty(self):
        self._ticket(1, priority=Ticket.Priority.LOW)

        data = self.client.get(self.url).data

        self.assertEqual(
            {r["name"] for r in data["priority"]}, {"Critical", "High", "Medium", "Low"}
        )
        self.assertEqual(
            {r["name"] for r in data["status"]},
            {"Open", "In Progress", "On Hold", "Resolved", "Closed"},
        )

    def test_origin_groups_by_connector_and_names_unattached_tickets(self):
        self._ticket(1, connector=self.zendesk)
        self._ticket(2, connector=self.zendesk)
        self._ticket(3)

        origin = {r["name"]: r["value"] for r in self.client.get(self.url).data["origin"]}

        self.assertEqual(origin["Zendesk"], 2)
        self.assertEqual(origin["Revenact"], 1)

    def test_assignees_carry_display_keys_and_a_precomputed_total(self):
        self._ticket(1, assignee_name="Ada", status=Ticket.Status.RESOLVED)
        self._ticket(2, assignee_name="Ada", status=Ticket.Status.OPEN)
        self._ticket(3, assignee_name="Grace", status=Ticket.Status.ON_HOLD)

        rows = {r["name"]: r for r in self.client.get(self.url).data["assignees"]}

        self.assertEqual(rows["Ada"]["Resolved"], 1)
        self.assertEqual(rows["Ada"]["Open"], 1)
        self.assertEqual(rows["Ada"]["total"], 2)
        self.assertEqual(rows["Grace"]["On Hold"], 1)

    def test_assignees_are_ordered_smallest_last_for_a_horizontal_chart(self):
        """Recharts draws a horizontal bar chart's first row at the
        bottom, so the busiest assignee has to come last to appear on
        top."""
        for n in range(3):
            self._ticket(n, assignee_name="Busy")
        self._ticket(9, assignee_name="Quiet")

        names = [r["name"] for r in self.client.get(self.url).data["assignees"]]

        self.assertEqual(names, ["Quiet", "Busy"])

    def test_the_timeline_buckets_by_month_of_opened_at(self):
        self._ticket(1, opened_at="2026-03-01", sentiment=Ticket.Sentiment.POSITIVE)
        self._ticket(2, opened_at="2026-03-28", sentiment=Ticket.Sentiment.NEGATIVE)
        self._ticket(3, opened_at="2026-05-02", sentiment=Ticket.Sentiment.POSITIVE)

        timeline = self.client.get(self.url).data["sentiment_timeline"]

        self.assertEqual(
            timeline,
            [
                {"date": "Mar 2026", "positive": 1, "negative": 1},
                {"date": "May 2026", "positive": 1, "negative": 0},
            ],
        )

    # ── the KPIs ─────────────────────────────────────────────────────

    def test_lifetime_and_resolution_rate(self):
        self._ticket(
            1, status=Ticket.Status.RESOLVED, opened_at="2026-03-01", resolved_at="2026-03-11"
        )
        self._ticket(
            2, status=Ticket.Status.CLOSED, opened_at="2026-03-01", resolved_at="2026-03-05"
        )
        self._ticket(3, status=Ticket.Status.OPEN)
        self._ticket(4, status=Ticket.Status.ON_HOLD)

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["avg_lifetime_days"], 7.0)
        self.assertEqual(kpis["resolution_rate"], 50.0)
        self.assertEqual(kpis["on_hold"], 1)

    def test_lifetime_is_none_rather_than_zero_when_nothing_has_resolved(self):
        """Zero days would read as "everything closed instantly"; an
        average of no samples is undefined."""
        self._ticket(1, status=Ticket.Status.OPEN)

        self.assertIsNone(self.client.get(self.url).data["kpis"]["avg_lifetime_days"])

    def test_an_empty_set_does_not_divide_by_zero(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["kpis"]["total"], 0)
        self.assertEqual(response.data["kpis"]["resolution_rate"], 0)

    def test_open_count_and_oldest_open_days_with_mixed_tickets(self):
        """open_count counts tickets not in RESOLVED_STATUSES;
        oldest_open_days is days from the oldest open ticket."""
        from django.utils import timezone

        today = timezone.localdate()
        three_days_ago = today - timedelta(days=3)
        five_days_ago = today - timedelta(days=5)

        self._ticket(1, status=Ticket.Status.OPEN, opened_at=five_days_ago)
        self._ticket(2, status=Ticket.Status.IN_PROGRESS, opened_at=three_days_ago)
        self._ticket(3, status=Ticket.Status.RESOLVED, opened_at=today)
        self._ticket(4, status=Ticket.Status.CLOSED, opened_at=today)

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["open_count"], 2)
        self.assertEqual(kpis["oldest_open_days"], 5)

    def test_oldest_open_days_is_none_when_no_open_tickets(self):
        """When all tickets are resolved/closed, oldest_open_days is None."""
        self._ticket(1, status=Ticket.Status.RESOLVED)
        self._ticket(2, status=Ticket.Status.CLOSED)

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["open_count"], 0)
        self.assertIsNone(kpis["oldest_open_days"])

    def test_open_count_and_oldest_open_days_in_empty_set(self):
        """With no tickets at all, both are 0 and None."""
        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["open_count"], 0)
        self.assertIsNone(kpis["oldest_open_days"])

    # ── filters ──────────────────────────────────────────────────────

    def test_date_range_filters_on_opened_at(self):
        self._ticket(1, opened_at="2026-01-15")
        self._ticket(2, opened_at="2026-06-15")

        data = self.client.get(f"{self.url}?from=2026-06-01&to=2026-06-30").data

        self.assertEqual(data["kpis"]["total"], 1)

    def test_priority_filter(self):
        self._ticket(1, priority=Ticket.Priority.LOW)
        self._ticket(2, priority=Ticket.Priority.CRITICAL)

        self.assertEqual(self.client.get(f"{self.url}?priority=critical").data["kpis"]["total"], 1)

    def test_connector_filter(self):
        self._ticket(1, connector=self.zendesk)
        self._ticket(2)

        data = self.client.get(f"{self.url}?connector={self.zendesk.id}").data

        self.assertEqual(data["kpis"]["total"], 1)

    def test_customer_filter(self):
        other = Customer.objects.create(organisation=self.org, name="Also Mine", owner=self.csm)
        self._ticket(1)
        self._ticket(2, customer=other)

        self.assertEqual(
            self.client.get(f"{self.url}?customer={self.mine.id}").data["kpis"]["total"], 1
        )

    def test_a_nonsense_filter_is_ignored_rather_than_a_400(self):
        """House convention: a dashboard should render with the filters
        it understood, not refuse to draw."""
        self._ticket(1)

        response = self.client.get(f"{self.url}?priority=banana&from=nonsense&owner=abc")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["kpis"]["total"], 1)

    # ── visibility ───────────────────────────────────────────────────

    def test_another_owners_tickets_are_excluded(self):
        self._ticket(1)
        self._ticket(2, customer=self.theirs)

        self.assertEqual(self.client.get(self.url).data["kpis"]["total"], 1)

    def test_filter_options_are_scoped_too(self):
        """A CSM shouldn't be offered a company they can't see as a
        filter option."""
        names = {c["name"] for c in self.client.get(self.url).data["filters"]["customers"]}

        self.assertIn("Mine", names)
        self.assertNotIn("Theirs", names)

    # ── drill ────────────────────────────────────────────────────────

    def test_drill_lists_companies_with_their_ticket_counts(self):
        also = Customer.objects.create(organisation=self.org, name="Also mine", owner=self.csm)
        self._ticket(1)
        self._ticket(2)
        self._ticket(3, customer=also)
        self._ticket(4, priority=Ticket.Priority.LOW)
        body = self.client.get(self.url, {"drill": "priority:high"}).json()
        self.assertEqual(set(body), {"drill", "currency"})
        companies = body["drill"]["companies"]
        self.assertEqual(
            [(c["name"], c["value"]) for c in companies], [("Mine", 2), ("Also mine", 1)]
        )
        self.assertEqual(body["drill"]["value_label"], "tickets")

    def test_drill_respects_the_other_filters_and_the_viewers_book(self):
        self._ticket(1)
        self._ticket(2, customer=self.theirs)
        body = self.client.get(self.url, {"drill": "all", "from": "2026-02-01"}).json()
        self.assertEqual([c["name"] for c in body["drill"]["companies"]], ["Mine"])
        body = self.client.get(self.url, {"drill": "all", "from": "2026-04-01"}).json()
        self.assertEqual(body["drill"]["companies"], [])

    def test_each_segment_kind(self):
        self._ticket(
            1,
            status=Ticket.Status.ON_HOLD,
            sentiment=Ticket.Sentiment.NEGATIVE,
            connector=self.zendesk,
            assignee_name="Ada",
        )
        self._ticket(2)
        for drill, expected in [
            ("on_hold", 1),
            ("sentiment:negative", 1),
            ("status:on-hold", 1),
            (f"origin:{self.zendesk.pk}", 1),
            ("origin:none", 1),
            ("assignee:Ada", 1),
        ]:
            with self.subTest(drill=drill):
                companies = self.client.get(self.url, {"drill": drill}).json()["drill"]["companies"]
                self.assertEqual([c["value"] for c in companies], [expected])

    def _shared_account_ticket(self, sibling_owner=None, account_owner=None):
        """An account-level ticket on an account Mine shares with a sibling
        the viewer can also see (unowned, or their own)."""
        sibling = Customer.objects.create(
            organisation=self.org, name="Sibling", owner=sibling_owner
        )
        account = Account.objects.create(name="Shared", owner=account_owner)
        account.customers.add(self.mine, sibling)
        self._ticket(1, customer=None, account=account)
        return account

    def test_a_customer_filter_drills_to_that_customer_only(self):
        self._shared_account_ticket(sibling_owner=self.csm)
        both = self.client.get(self.url, {"drill": "all"}).json()["drill"]["companies"]
        self.assertEqual(sorted(c["name"] for c in both), ["Mine", "Sibling"])
        body = self.client.get(self.url, {"drill": "all", "customer": self.mine.pk}).json()
        self.assertEqual([c["name"] for c in body["drill"]["companies"]], ["Mine"])

    def test_an_owner_filter_drills_to_that_owners_customers_only(self):
        self._shared_account_ticket(account_owner=self.csm)
        body = self.client.get(self.url, {"drill": "all", "owner": self.csm.pk}).json()
        self.assertEqual([c["name"] for c in body["drill"]["companies"]], ["Mine"])

    def test_an_account_filter_drills_to_that_accounts_customers_only(self):
        account = self._shared_account_ticket(sibling_owner=self.csm)
        elsewhere = Customer.objects.create(organisation=self.org, name="Elsewhere", owner=self.csm)
        other_account = Account.objects.create(name="Other")
        other_account.customers.add(elsewhere)
        self._ticket(2, customer=None, account=other_account)
        body = self.client.get(self.url, {"drill": "all", "account": account.pk}).json()
        self.assertEqual(sorted(c["name"] for c in body["drill"]["companies"]), ["Mine", "Sibling"])

    def test_a_shared_account_ticket_drills_to_the_viewers_book_only(self):
        """The twice-filter end to end: the account-level ticket is visible to
        Carl through his own customer, and fans out to both customers of the
        account — but Theirs is outside his book, so only someone who sees
        every account gets it in the list."""
        from services.accounts.capabilities import Capability
        from services.accounts.models import Role

        account = Account.objects.create(name="Joint venture")
        account.customers.add(self.mine, self.theirs)
        self._ticket(1, customer=None, account=account)

        body = self.client.get(self.url, {"drill": "all"}).json()
        self.assertEqual([c["name"] for c in body["drill"]["companies"]], ["Mine"])

        lead = Role.objects.create(
            organisation=self.org,
            name="Lead",
            slug="lead",
            permissions=[Capability.VIEW_ALL_ACCOUNTS],
        )
        viewer = User.objects.create_user(
            email="lee@acme.io", password="supersecret1", name="Lee", organisation=self.org
        )
        User.objects.filter(pk=viewer.pk).update(role=lead)
        viewer.refresh_from_db()
        self.client.force_authenticate(viewer)
        body = self.client.get(self.url, {"drill": "all"}).json()
        self.assertEqual(sorted(c["name"] for c in body["drill"]["companies"]), ["Mine", "Theirs"])

    def test_a_bad_drill_returns_the_normal_stats(self):
        self._ticket(1)
        for drill in ["priority:nope", "bogus", "all:x", "origin:abc"]:
            with self.subTest(drill=drill):
                body = self.client.get(self.url, {"drill": drill}).json()
                self.assertIn("kpis", body)
                self.assertNotIn("drill", body)

    # ── owner=unassigned ────────────────────────────────────────────────

    def test_owner_unassigned_narrows_the_stats_to_unowned_customers(self):
        """Mirrors services.customers.forecast's `owner=unassigned`
        (test_forecast.py) and ticket_filters.filtered_tickets's own unit
        test: `/tickets/stats/` reads the same filter, so it must agree."""
        nobody = Customer.objects.create(organisation=self.org, name="Nobody's")
        self._ticket(1, customer=nobody)
        self._ticket(2, customer=self.mine)

        data = self.client.get(self.url, {"owner": "unassigned"}).data

        self.assertEqual(data["kpis"]["total"], 1)


class PulseFieldsAPITests(APITestCase):
    """The AI pulse moved from a stored category to a stored 1-5 value with the
    category derived. `ai_pulse_score` is part of the shipped API — the
    frontend's AI_PULSE_LABELS reads it and clients POST it — so these pin that
    both names keep working in both directions."""

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

    def _detail_url(self, customer):
        return f"{self.url}{customer.id}/"

    def test_create_accepts_the_category_and_stores_the_value(self):
        response = self.client.post(
            self.url, {"name": "Globex", "ai_pulse_score": "satisfied"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["ai_pulse_score"], "satisfied")
        self.assertEqual(response.data["ai_pulse_value"], 4)
        self.assertEqual(Customer.objects.get(name="Globex").ai_pulse_value, 4)

    def test_create_accepts_the_value_and_derives_the_category(self):
        response = self.client.post(
            self.url, {"name": "Initech", "ai_pulse_value": 2}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["ai_pulse_value"], 2)
        self.assertEqual(response.data["ai_pulse_score"], "high_risk")

    def test_every_category_round_trips_back_to_itself(self):
        for category in ("very_satisfied", "satisfied", "moderate", "high_risk"):
            response = self.client.post(
                self.url, {"name": f"Co {category}", "ai_pulse_score": category}, format="json"
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data["ai_pulse_score"], category)

    def test_unscored_reads_back_blank(self):
        response = self.client.post(self.url, {"name": "Blank Co"}, format="json")
        self.assertIsNone(response.data["ai_pulse_value"])
        self.assertEqual(response.data["ai_pulse_score"], "")

    def test_rejects_sending_both_names_at_once(self):
        # They write the same column, so a request carrying both is ambiguous.
        # Resolving it silently would mean one of the two is quietly ignored.
        response = self.client.post(
            self.url,
            {"name": "Globex", "ai_pulse_score": "satisfied", "ai_pulse_value": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ai_pulse_score", response.data)

    def test_rejects_an_unknown_category(self):
        response = self.client.post(
            self.url, {"name": "Globex", "ai_pulse_score": "delighted"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_a_value_off_the_scale(self):
        response = self.client.post(
            self.url, {"name": "Globex", "ai_pulse_value": 9}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_setting_the_csm_pulse_stamps_when_it_changed(self):
        customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.assertIsNone(customer.csm_pulse_modified_at)

        response = self.client.patch(
            self._detail_url(customer), {"csm_pulse_score": 4}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["csm_pulse_score"], 4)
        self.assertIsNotNone(response.data["csm_pulse_modified_at"])

    def test_resaving_the_same_csm_pulse_does_not_restamp_it(self):
        # The stamp answers "how stale is this CSM's read" — a PATCH that
        # didn't move the number must not make a stale read look fresh.
        customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.client.patch(self._detail_url(customer), {"csm_pulse_score": 4}, format="json")
        customer.refresh_from_db()
        first_stamp = customer.csm_pulse_modified_at

        self.client.patch(self._detail_url(customer), {"csm_pulse_score": 4}, format="json")
        customer.refresh_from_db()
        self.assertEqual(customer.csm_pulse_modified_at, first_stamp)

    def test_editing_something_else_does_not_stamp_the_csm_pulse(self):
        customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.client.patch(self._detail_url(customer), {"name": "Globex Corp"}, format="json")
        customer.refresh_from_db()
        self.assertIsNone(customer.csm_pulse_modified_at)

    def test_the_stamp_is_not_client_settable(self):
        customer = Customer.objects.create(organisation=self.org, name="Globex")
        response = self.client.patch(
            self._detail_url(customer),
            {"csm_pulse_score": 3, "csm_pulse_modified_at": "2020-01-01T00:00:00Z"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        customer.refresh_from_db()
        self.assertNotEqual(str(customer.csm_pulse_modified_at)[:4], "2020")

    def test_accounts_expose_the_same_pair(self):
        customer = Customer.objects.create(organisation=self.org, name="Globex")
        response = self.client.post(
            f"/api/v1/customers/{customer.id}/accounts/",
            {"name": "EMEA", "ai_pulse_score": "moderate", "csm_pulse_score": 5},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["ai_pulse_value"], 3)
        self.assertEqual(response.data["ai_pulse_score"], "moderate")
        self.assertEqual(response.data["csm_pulse_score"], 5)
        self.assertIsNotNone(response.data["csm_pulse_modified_at"])


class HealthRubricAPITests(APITestCase):
    """`health_score` is computed from the five components in health.py now,
    and writing it pins an override instead of setting the number directly."""

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

    def _customer(self, **kwargs):
        return Customer.objects.create(organisation=self.org, name="Globex", **kwargs)

    def _detail(self, customer):
        return f"{self.url}{customer.id}/"

    def _get(self, customer):
        return self.client.get(self._detail(customer)).data

    def test_exposes_every_component_of_the_score(self):
        data = self._get(self._customer())
        labels = [row["label"] for row in data["health_breakdown"]]
        self.assertEqual(
            labels,
            [
                "Customer Touch",
                "AI Pulse",
                "Licence Utilization",
                "Aggregate Adoption Score",
                "Support Tickets Volume",
            ],
        )

    def test_the_components_add_up_to_the_score(self):
        # The whole reason the rubric exists: the popover used to derive its
        # rows *from* the headline number, so they could never reconcile.
        customer = self._customer(
            ai_pulse_value=4,
            total_active_seats=80,
            total_contracted_seats=100,
            primary_product=Product.objects.create(organisation=self.org, name="Product A"),
            additional_products_count=1,
        )
        customer.recalculate_health()

        data = self._get(customer)
        measured = [r for r in data["health_breakdown"] if r["available"]]
        self.assertEqual(len(measured), len(data["health_breakdown"]))
        self.assertEqual(sum(Decimal(r["points"]) for r in measured), Decimal(data["health_score"]))

    def test_marks_a_component_it_cannot_measure(self):
        # No seat figures recorded — the component is reported unavailable
        # rather than scored zero, and is left out of the total.
        data = self._get(self._customer(total_contracted_seats=None))
        licence = next(r for r in data["health_breakdown"] if r["key"] == "licence_utilization")
        self.assertFalse(licence["available"])
        self.assertEqual(licence["points"], "0.0")

    def test_a_fresh_customer_is_not_punished_for_having_no_activity(self):
        # Touch is measured from when the row arrived if it has no activities,
        # so a new logo doesn't lose the heaviest component on day one.
        data = self._get(self._customer())
        touch = next(r for r in data["health_breakdown"] if r["key"] == "customer_touch")
        self.assertTrue(touch["available"])
        self.assertEqual(touch["ratio"], 1.0)

    def test_score_is_recalculated_on_create(self):
        response = self.client.post(
            self.url,
            {"name": "Initech", "ai_pulse_value": 1, "primary_product": None},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data["health_score_is_overridden"])
        # Not the model's 5.0 default — the rubric ran.
        customer = Customer.objects.get(name="Initech")
        self.assertEqual(Decimal(response.data["health_score"]), customer.health_score)

    def test_writing_the_score_pins_it_as_an_override(self):
        customer = self._customer(ai_pulse_value=1)
        response = self.client.patch(self._detail(customer), {"health_score": "9.9"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["health_score"], "9.9")
        self.assertTrue(response.data["health_score_is_overridden"])
        customer.refresh_from_db()
        self.assertEqual(customer.health_score_override, Decimal("9.9"))
        self.assertEqual(customer.health_score, Decimal("9.9"))

    def test_an_override_survives_an_unrelated_edit(self):
        customer = self._customer()
        self.client.patch(self._detail(customer), {"health_score": "9.9"}, format="json")
        self.client.patch(self._detail(customer), {"name": "Globex Corp"}, format="json")

        customer.refresh_from_db()
        self.assertEqual(customer.health_score, Decimal("9.9"))

    def test_clearing_the_override_hands_the_customer_back_to_the_rubric(self):
        customer = self._customer(ai_pulse_value=1)
        self.client.patch(self._detail(customer), {"health_score": "9.9"}, format="json")

        response = self.client.patch(self._detail(customer), {"health_score": None}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["health_score_is_overridden"])

        customer.refresh_from_db()
        self.assertIsNone(customer.health_score_override)
        self.assertNotEqual(customer.health_score, Decimal("9.9"))

    def test_rejects_a_score_off_the_scale(self):
        customer = self._customer()
        for value in ("-1.0", "11.0"):
            response = self.client.patch(
                self._detail(customer), {"health_score": value}, format="json"
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_health_category_follows_the_computed_score(self):
        customer = self._customer()
        self.client.patch(self._detail(customer), {"health_score": "2.0"}, format="json")
        self.assertEqual(self._get(customer)["health_category"], "poor")

        self.client.patch(self._detail(customer), {"health_score": "9.0"}, format="json")
        self.assertEqual(self._get(customer)["health_category"], "good")

    def test_listing_many_customers_does_not_query_per_row(self):
        # health_breakdown needs a last-touch date and an open-ticket count;
        # without the annotation that is two extra queries per row.
        for i in range(8):
            Customer.objects.create(organisation=self.org, name=f"Co {i}")

        # Four for the page however many rows it has: resolving the caller's
        # membership (once per request, memoised — see
        # services.identity.context), a COUNT, the SELECT carrying both health
        # subqueries, and one prefetch of every CSAT response. The per-row
        # breakdowns cost nothing further, which is what this guards.
        with self.assertNumQueries(4):
            response = self.client.get(self.url)
        self.assertEqual(len(response.data["results"]), 8)

    def test_the_query_count_does_not_grow_with_the_page(self):
        # The assertion above is only meaningful if it holds at a larger size:
        # a fixed number for 8 rows could still be per-row at 30. Same four as
        # above: the count must not move with the page.
        for i in range(30):
            Customer.objects.create(organisation=self.org, name=f"Big {i}")

        with self.assertNumQueries(4):
            response = self.client.get(self.url)
        self.assertGreater(len(response.data["results"]), 8)


class CustomerHealthViewTests(APITestCase):
    """One request serving all four Health Overview tabs."""

    url = "/api/v1/customers/health/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.other_org = Organisation.objects.create(name="Globex")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.customer = Customer.objects.create(
            organisation=self.org,
            name="Hyatt",
            csm_pulse_score=4,
            ai_pulse_value=2,
            total_active_seats=822,
        )
        self.client.force_authenticate(self.admin)

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returns_the_whole_book_unpaginated(self):
        # Every tab aggregates over all of it — a paged triage queue would
        # rank nothing, and a paged flow chart would show the wrong movement.
        for i in range(30):
            Customer.objects.create(organisation=self.org, name=f"Co {i}")

        data = self.client.get(self.url).data
        self.assertEqual(data["count"], 31)
        self.assertEqual(len(data["results"]), 31)
        self.assertFalse(data["truncated"])

    def test_is_scoped_to_the_callers_organisation(self):
        Customer.objects.create(organisation=self.other_org, name="Not Mine")
        names = [r["name"] for r in self.client.get(self.url).data["results"]]
        self.assertIn("Hyatt", names)
        self.assertNotIn("Not Mine", names)

    def test_excludes_archived_customers(self):
        Customer.objects.create(organisation=self.org, name="Gone", is_archived=True)
        names = [r["name"] for r in self.client.get(self.url).data["results"]]
        self.assertNotIn("Gone", names)

    def test_carries_the_fields_the_dashboard_reads(self):
        row = self.client.get(self.url).data["results"][0]
        for field in (
            "id",
            "name",
            "owner_id",
            "owner_name",
            "lifecycle_stage_display",
            "renewal_date",
            "health_score",
            "health_category",
            "csm_pulse_score",
            "csm_pulse_modified_at",
            "ai_pulse_value",
            "ai_pulse_reason",
            "total_active_seats",
            "history",
        ):
            self.assertIn(field, row)

    def test_keeps_an_unrated_pulse_null_rather_than_guessing(self):
        # "Not rated yet" is a real state. Sending 0 or 3 instead would make
        # the Divergence view read an unrated account as an agreed-on one.
        Customer.objects.create(organisation=self.org, name="Unrated")
        row = next(r for r in self.client.get(self.url).data["results"] if r["name"] == "Unrated")
        self.assertIsNone(row["csm_pulse_score"])
        self.assertIsNone(row["ai_pulse_value"])

    def test_includes_recorded_history_oldest_first(self):
        for day, score in ((31, "8.0"), (28, "5.0")):
            HealthSnapshot.objects.create(
                customer=self.customer,
                captured_on=date(2026, 1, 31) if day == 31 else date(2026, 2, 28),
                health_score=score,
            )
        row = next(r for r in self.client.get(self.url).data["results"] if r["name"] == "Hyatt")
        captured = [h["captured_on"] for h in row["history"]]
        self.assertEqual(captured, sorted(captured))
        self.assertEqual(row["history"][0]["health_category"], "good")

    def test_history_months_trims_the_payload(self):
        today = timezone.localdate()
        for months_back in (1, 10):
            HealthSnapshot.objects.create(
                customer=self.customer,
                captured_on=today - timedelta(days=31 * months_back),
                health_score="5.0",
            )

        full = self.client.get(self.url).data["results"]
        trimmed = self.client.get(f"{self.url}?history_months=3").data

        self.assertEqual(len(next(r for r in full if r["name"] == "Hyatt")["history"]), 2)
        self.assertEqual(
            len(next(r for r in trimmed["results"] if r["name"] == "Hyatt")["history"]), 1
        )
        self.assertEqual(trimmed["history_months"], 3)

    def test_rejects_a_nonsense_history_window(self):
        for value in ("0", "-3", "soon"):
            response = self.client.get(f"{self.url}?history_months={value}")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_does_not_query_per_customer(self):
        # Each row carries its owner, a year of snapshots, a last-touch date
        # and an ARR conversion; without the annotations and the pre-fetched
        # rate table that is four more queries per customer.
        for i in range(10):
            other = Customer.objects.create(organisation=self.org, name=f"Co {i}")
            HealthSnapshot.objects.create(
                customer=other, captured_on=date(2026, 1, 31), health_score="7.0"
            )

        # Five, none of them per row: resolving the caller's membership (once
        # per request, memoised — see services.identity.context), the customers
        # (owners joined, touch/tickets annotated as subqueries), every CSAT
        # survey at once (with_health_inputs prefetches those), every snapshot
        # at once, and one FX rate table for the whole page.
        with self.assertNumQueries(5):
            self.client.get(self.url)

    def test_each_row_carries_the_shared_churn_rule_and_its_reasons(self):
        """Served rather than computed in the browser, so the Renewal tab and
        the Revenue Forecast can't drift apart about the same account."""
        Activity.objects.create(
            customer=self.customer,
            type=Activity.ActivityType.HEALTH_CHECK_REVIEW,
            occurred_at=timezone.localdate() - timedelta(days=120),
        )

        row = next(r for r in self.client.get(self.url).data["results"] if r["name"] == "Hyatt")

        from services.customers import churn

        expected, factors = churn.risk_of_loss(self.customer, days_since_touch=120)
        self.assertEqual(row["risk_of_loss"], expected)
        self.assertEqual([f["label"] for f in row["risk_factors"]], [f["label"] for f in factors])
        self.assertIn("No contact in 120 days", [f["label"] for f in row["risk_factors"]])

    def test_triage_score_is_computed_from_health_and_renewal(self):
        """Average health (22) + a renewal inside 90 days (18) lands exactly
        on the action threshold — see services.customers.triage. A fresh
        customer, not `self.customer`, so its own csm/ai pulse gap (set in
        setUp) doesn't add a third factor to the sum."""
        customer = Customer.objects.create(
            organisation=self.org,
            name="Riverside",
            renewal_date=timezone.localdate() + timedelta(days=30),
        )

        row = next(r for r in self.client.get(self.url).data["results"] if r["name"] == "Riverside")

        self.assertEqual(row["triage_score"], 40)
        labels = [f["label"] for f in row["triage_factors"]]
        self.assertIn("Health is Average", labels)
        self.assertIn("Renews in 30d", labels)
        self.assertEqual(row["triage_direction"], "unknown")
        self.assertEqual(customer.health_category, "average")

    def test_the_owner_comes_back_as_an_id_as_well_as_a_name(self):
        """The Primary Owner filter keys on the id: two CSMs sharing a name is
        ordinary, and filtering by label would silently merge their books."""
        owner = User.objects.create_user(
            email="gerry@acme.io",
            password="supersecret1",
            name="Gerry Hill",
            organisation=self.org,
            role=User.Role.CSM,
        )
        Customer.objects.create(organisation=self.org, name="Owned", owner=owner)

        rows = {r["name"]: r for r in self.client.get(self.url).data["results"]}

        self.assertEqual(rows["Owned"]["owner_id"], owner.id)
        self.assertEqual(rows["Owned"]["owner_name"], "Gerry Hill")
        # An unowned customer keeps both as null rather than inventing a
        # placeholder the client would have to recognise.
        self.assertIsNone(rows["Hyatt"]["owner_id"])
        self.assertIsNone(rows["Hyatt"]["owner_name"])

    # ── money and staleness, for the Renewal Date tab ─────────────────

    def test_arr_comes_back_in_the_organisations_own_currency(self):
        from services.fx_rates.models import FxRate

        self.org.currency = "USD"
        self.org.save(update_fields=["currency"])
        FxRate.objects.create(
            organisation=self.org, currency="EUR", rate_to_org_currency=Decimal("1.10")
        )
        Customer.objects.create(
            organisation=self.org,
            name="Berlin GmbH",
            currency="EUR",
            arr_billed_at_account=Decimal("100000"),
        )

        rows = {r["name"]: r for r in self.client.get(self.url).data["results"]}

        self.assertEqual(rows["Berlin GmbH"]["arr"], 110000.0)

    def test_an_unconvertible_arr_is_null_and_counted_rather_than_summed_as_is(self):
        # Treating 100,000 JPY as 100,000 USD is the one outcome worse than
        # leaving the row out of the total.
        Customer.objects.create(
            organisation=self.org,
            name="Tokyo KK",
            currency="JPY",
            arr_billed_at_account=Decimal("100000"),
        )

        data = self.client.get(self.url).data
        rows = {r["name"]: r for r in data["results"]}

        self.assertIsNone(rows["Tokyo KK"]["arr"])
        self.assertEqual(data["unconverted_count"], 1)
        self.assertEqual(data["currency"], self.org.currency)

    def test_days_since_touch_is_the_same_number_the_health_score_uses(self):
        Activity.objects.create(
            customer=self.customer,
            type=Activity.ActivityType.HEALTH_CHECK_REVIEW,
            occurred_at=timezone.localdate() - timedelta(days=21),
        )

        row = next(r for r in self.client.get(self.url).data["results"] if r["name"] == "Hyatt")

        self.assertEqual(row["days_since_touch"], 21)
        self.assertEqual(row["days_since_touch"], self.customer.health_inputs()["days_since_touch"])

    def test_an_untouched_customer_is_measured_from_when_it_arrived(self):
        # A logo onboarded last week hasn't been neglected for a decade.
        new = Customer.objects.create(
            organisation=self.org,
            name="Fresh",
            joined_date=timezone.localdate() - timedelta(days=5),
        )

        row = next(r for r in self.client.get(self.url).data["results"] if r["name"] == "Fresh")

        self.assertEqual(row["days_since_touch"], 5)
        self.assertEqual(new.health_inputs()["days_since_touch"], 5)


class TaskStatusTests(APITestCase):
    """PATCH /api/v1/tasks/<id>/ — Cockpit ticking a task off."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.owner = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        self.customer = Customer.objects.create(
            organisation=self.org, name="Globex", owner=self.owner
        )
        self.task = Task.objects.create(
            customer=self.customer,
            title="Prepare QBR deck",
            assignee_name="Carl",
            due_date="2026-03-15",
            priority=Task.Priority.HIGH,
            status=Task.Status.PENDING,
        )

    def test_the_owner_completes_a_task_and_it_is_audited(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f"/api/v1/tasks/{self.task.id}/", {"status": "completed"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["parent_name"], "Globex")
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "completed")
        from core.models import AuditEvent

        event = AuditEvent.objects.get(action="task.update")
        self.assertEqual(
            event.metadata, {"fields": ["status"], "from": "pending", "to": "completed"}
        )

    def test_only_status_and_only_known_values(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f"/api/v1/tasks/{self.task.id}/", {"status": "done", "title": "Renamed"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "Prepare QBR deck")

    def test_another_tenant_cannot_see_the_task(self):
        rival = Organisation.objects.create(name="Rival")
        stranger = User.objects.create_user(
            email="x@rival.io", password="supersecret1", name="X", organisation=rival
        )
        self.client.force_authenticate(stranger)
        response = self.client.patch(
            f"/api/v1/tasks/{self.task.id}/", {"status": "completed"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "pending")
