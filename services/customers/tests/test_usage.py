"""The Usage Overview dashboard: seat utilisation, shelfware and capacity.

The arithmetic is asserted against fixtures rather than "a number came back",
because every figure on this screen is one someone will take into a renewal
conversation. Two rules get the most attention, because both are ways a usage
chart can lie confidently: a data gap must never read as 0% used, and a
book-wide rate must never be the average of per-account percentages.
"""

from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers import usage
from services.customers.models import Customer


class BandTests(SimpleTestCase):
    def test_boundaries_are_floor_inclusive(self):
        self.assertEqual(usage.band_for(0), "dormant")
        self.assertEqual(usage.band_for(24.9), "dormant")
        self.assertEqual(usage.band_for(25), "low")
        self.assertEqual(usage.band_for(75), "healthy")
        self.assertEqual(usage.band_for(90), "at_capacity")

    def test_over_deployment_is_a_band_not_an_error(self):
        # More actives than the contract allows is a real state — an expansion
        # trigger, and sometimes a compliance one. Clamping it to 100% would
        # hide the accounts most worth calling.
        self.assertEqual(usage.band_for(100), "over")
        self.assertEqual(usage.band_for(180), "over")

    def test_unmeasured_has_no_band(self):
        self.assertIsNone(usage.band_for(None))


class UsageStatsTests(APITestCase):
    url = "/api/v1/customers/usage/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
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
        self.client.force_authenticate(self.csm)

    def _customer(self, name, active, contracted, arr=100_000, **overrides):
        return Customer.objects.create(
            organisation=self.org,
            name=name,
            owner=overrides.pop("owner", self.csm),
            total_active_seats=active,
            total_contracted_seats=contracted,
            arr_billed_at_account=Decimal(arr),
            **overrides,
        )

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_another_owners_book_is_invisible(self):
        self._customer("Mine", 50, 100)
        self._customer("Theirs", 10, 100, owner=self.other)

        self.assertEqual(self.client.get(self.url).data["kpis"]["accounts"], 1)

    # ── the rate itself ──────────────────────────────────────────────

    def test_utilisation_is_seats_over_seats_not_an_average_of_percentages(self):
        """A ten-seat pilot must not weigh as much as a 1,500-seat rollout."""
        self._customer("Pilot", 10, 10)  # 100%
        self._customer("Rollout", 500, 1_000)  # 50%

        kpis = self.client.get(self.url).data["kpis"]

        # 510 / 1010, not (100 + 50) / 2.
        self.assertEqual(kpis["utilisation"], 50.5)
        self.assertEqual(kpis["contracted_seats"], 1_010)
        self.assertEqual(kpis["active_seats"], 510)

    def test_an_account_with_no_seats_recorded_is_unmeasured_not_zero(self):
        # Scoring a data gap as the worst possible number invents the most
        # alarming reading available and then charts it.
        self._customer("Known", 75, 100)
        self._customer("Blank", None, None)

        data = self.client.get(self.url).data

        self.assertEqual(data["kpis"]["utilisation"], 75.0)
        self.assertEqual(data["kpis"]["unmeasured_count"], 1)
        self.assertEqual(data["kpis"]["measured_count"], 1)
        self.assertEqual(sum(band["accounts"] for band in data["bands"]), 1)

    def test_an_unmeasured_account_is_not_plotted(self):
        # A point on the axis is a claim; "we don't know" isn't one.
        self._customer("Known", 75, 100)
        self._customer("Blank", None, None)

        names = [point["name"] for point in self.client.get(self.url).data["scatter"]]

        self.assertEqual(names, ["Known"])

    def test_zero_contracted_seats_is_unmeasured_rather_than_a_divide_by_zero(self):
        self._customer("Empty", 0, 0)

        data = self.client.get(self.url).data

        self.assertIsNone(data["kpis"]["utilisation"])
        self.assertEqual(data["kpis"]["unmeasured_count"], 1)

    # ── shelfware ────────────────────────────────────────────────────

    def test_shelfware_is_the_arr_attached_to_unused_seats(self):
        self._customer("Half", 50, 100, arr=100_000)

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["shelfware_arr"], 50_000.0)
        self.assertEqual(kpis["idle_seats"], 50)

    def test_a_well_used_account_carries_no_shelfware(self):
        # Four fifths used is used. Counting the rest would make the number
        # enormous and therefore meaningless.
        self._customer("Healthy", 80, 100, arr=100_000)

        self.assertEqual(self.client.get(self.url).data["kpis"]["shelfware_arr"], 0.0)

    def test_the_shelfware_list_ranks_by_money_not_by_worst_percentage(self):
        """A dormant ten-seat pilot is a worse percentage and a smaller
        problem than a half-used enterprise rollout."""
        self._customer("Tiny Pilot", 1, 10, arr=5_000)  # 10%
        self._customer("Big Rollout", 400, 1_000, arr=400_000)  # 40%

        names = [row["name"] for row in self.client.get(self.url).data["shelfware"]]

        self.assertEqual(names, ["Big Rollout", "Tiny Pilot"])

    def test_an_over_deployed_account_has_no_idle_seats(self):
        self._customer("Over", 120, 100)

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["idle_seats"], 0)
        self.assertEqual(kpis["shelfware_arr"], 0.0)

    # ── capacity ─────────────────────────────────────────────────────

    def test_capacity_counts_accounts_out_of_room(self):
        self._customer("Full", 95, 100, arr=200_000)
        self._customer("Roomy", 40, 100, arr=900_000)

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["at_capacity_count"], 1)
        self.assertEqual(kpis["at_capacity_arr"], 200_000.0)

    def test_the_capacity_list_ranks_by_what_the_account_already_pays(self):
        # The bigger the account, the bigger the expansion its ceiling implies.
        self._customer("Small But Full", 99, 100, arr=20_000)
        self._customer("Large And Full", 91, 100, arr=500_000)

        names = [row["name"] for row in self.client.get(self.url).data["at_capacity"]]

        self.assertEqual(names, ["Large And Full", "Small But Full"])

    # ── money and shape ──────────────────────────────────────────────

    def test_an_unconvertible_arr_is_counted_but_left_out_of_the_money(self):
        self._customer("Local", 50, 100, arr=100_000)
        self._customer("Tokyo", 50, 100, arr=100_000, currency="JPY")

        data = self.client.get(self.url).data

        self.assertEqual(data["kpis"]["unpriced_count"], 1)
        # Seats still count — the gap is in the pricing, not the usage.
        self.assertEqual(data["kpis"]["contracted_seats"], 200)
        self.assertEqual(data["kpis"]["shelfware_arr"], 50_000.0)

    def test_every_band_comes_back_even_when_empty(self):
        # A bar that vanishes when a band empties is harder to read than one
        # that sits at zero, and the shape of the book is the point.
        self._customer("One", 50, 100)

        names = [band["key"] for band in self.client.get(self.url).data["bands"]]

        self.assertEqual(names, [key for key, _l, _f, _c in usage.BANDS])

    def test_adoption_counts_primary_plus_additional_products(self):
        self._customer("Single", 50, 100, primary_product="Product A")
        self._customer("Broad", 50, 100, primary_product="Product A", additional_products_count=3)

        adoption = {
            row["key"]: row["accounts"] for row in self.client.get(self.url).data["adoption"]
        }

        self.assertEqual(adoption["1"], 1)
        self.assertEqual(adoption["4+"], 1)

    # ── filters ──────────────────────────────────────────────────────

    def test_the_owner_filter_narrows_the_book(self):
        self._customer("Mine", 50, 100)
        Customer.objects.create(
            organisation=self.org, name="Unowned", total_active_seats=10, total_contracted_seats=100
        )

        data = self.client.get(self.url, {"owner": str(self.csm.id)}).data

        self.assertEqual(data["kpis"]["accounts"], 1)

    def test_unassigned_is_a_filter_value_of_its_own(self):
        self._customer("Mine", 50, 100)
        Customer.objects.create(
            organisation=self.org, name="Unowned", total_active_seats=10, total_contracted_seats=100
        )

        data = self.client.get(self.url, {"owner": "unassigned"}).data

        self.assertEqual(data["kpis"]["accounts"], 1)
        self.assertEqual(data["scatter"][0]["name"], "Unowned")

    def test_lifecycle_and_customer_filters_narrow_it_too(self):
        onboarding = self._customer(
            "New Co", 20, 100, lifecycle_stage=Customer.LifecycleStage.ONBOARDING
        )
        self._customer("Live Co", 80, 100, lifecycle_stage=Customer.LifecycleStage.LIVE)

        self.assertEqual(
            self.client.get(self.url, {"lifecycle": "onboarding"}).data["kpis"]["accounts"], 1
        )
        self.assertEqual(
            self.client.get(self.url, {"customer": onboarding.id}).data["kpis"]["accounts"], 1
        )

    def test_a_bad_filter_value_is_ignored_rather_than_returning_nothing(self):
        self._customer("Mine", 50, 100)

        self.assertEqual(self.client.get(self.url, {"owner": "abc"}).data["kpis"]["accounts"], 1)
        self.assertEqual(
            self.client.get(self.url, {"lifecycle": "nonsense"}).data["kpis"]["accounts"], 1
        )

    def test_filter_options_only_offer_values_in_the_visible_book(self):
        self._customer("Mine", 50, 100)
        self._customer("Theirs", 50, 100, owner=self.other)

        options = self.client.get(self.url).data["filters"]

        self.assertEqual([o["name"] for o in options["customers"]], ["Mine"])
        self.assertEqual([o["name"] for o in options["owners"]], ["Carl"])

    def test_archived_customers_are_left_out(self):
        self._customer("Live", 50, 100)
        self._customer("Gone", 50, 100, is_archived=True)

        self.assertEqual(self.client.get(self.url).data["kpis"]["accounts"], 1)
