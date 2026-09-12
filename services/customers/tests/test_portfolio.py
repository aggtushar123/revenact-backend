"""Customer Overview: composition, concentration, cohorts and churn.

The tests are about the population each figure speaks for, because that is what
makes this screen different from every other one: it is the only view that
counts customers you no longer have, and getting that wrong produces retention
figures that are always 100%.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers import portfolio, segments
from services.customers.models import Customer

Reason = Customer.ChurnReason


class SegmentTests(TestCase):
    def test_bands_are_floor_inclusive_and_open_at_the_top(self):
        self.assertEqual(segments.bracket_for(0), "under_25k")
        self.assertEqual(segments.bracket_for(25_000), "25k_50k")
        self.assertEqual(segments.bracket_for(100_000), "over_100k")
        self.assertEqual(segments.bracket_for(5_000_000), "over_100k")

    def test_an_unpriced_account_is_unplaced_rather_than_small(self):
        # Null ARR means no exchange rate, not a small customer.
        self.assertIsNone(segments.bracket_for(None))


class CustomerOverviewTests(APITestCase):
    url = "/api/v1/customers/overview/"

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
        self.today = timezone.localdate()
        self.client.force_authenticate(self.csm)

    def _customer(self, name, arr=100_000, **overrides):
        return Customer.objects.create(
            organisation=self.org,
            name=name,
            owner=overrides.pop("owner", self.csm),
            arr_billed_at_account=Decimal(arr),
            **overrides,
        )

    def _reasons(self):
        return {row["value"]: row for row in self.client.get(self.url).data["churn_reasons"]}

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_another_owners_book_is_invisible(self):
        self._customer("Mine")
        self._customer("Theirs", owner=self.other)

        self.assertEqual(self.client.get(self.url).data["kpis"]["active"], 1)

    # ── the population each figure speaks for ────────────────────────

    def test_churned_customers_are_counted_here_unlike_everywhere_else(self):
        """Every other dashboard excludes them. Retention computed over
        survivors only would return 100% every time."""
        self._customer("Live", 100_000)
        self._customer("Left", 50_000, churn_date=self.today - timedelta(days=30))

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["active"], 1)
        self.assertEqual(kpis["active_arr"], 100_000.0)
        self.assertEqual(kpis["churned"], 1)
        self.assertEqual(kpis["churned_arr"], 50_000.0)
        self.assertEqual(kpis["logo_retention"], 50.0)

    def test_churn_means_a_date_not_an_archive_flag(self):
        """Archiving is a filing decision — a duplicate row gets archived.
        Counting it as churn would pad every retention denominator with
        housekeeping."""
        self._customer("Live")
        self._customer("Filed away", is_archived=True)

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["churned"], 0)
        # Nor is an archived row active: it is neither, and the retention
        # figure is about logos that left, not rows that were tidied.
        self.assertEqual(kpis["active"], 1)
        self.assertEqual(kpis["logo_retention"], 100.0)

    def test_the_twelve_month_figure_is_separate_from_all_time(self):
        self._customer("Recent", 40_000, churn_date=self.today - timedelta(days=100))
        self._customer("Ancient", 90_000, churn_date=self.today - timedelta(days=900))

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["churned"], 2)
        self.assertEqual(kpis["churned_12m"], 1)
        self.assertEqual(kpis["churned_arr_12m"], 40_000.0)

    def test_retention_and_average_are_null_rather_than_flattering_on_an_empty_book(self):
        kpis = self.client.get(self.url).data["kpis"]

        self.assertIsNone(kpis["logo_retention"])
        self.assertIsNone(kpis["average_arr"])

    # ── concentration ────────────────────────────────────────────────

    def test_concentration_ranks_by_arr_with_a_running_share(self):
        self._customer("Whale", 600_000)
        self._customer("Middle", 300_000)
        self._customer("Minnow", 100_000)

        concentration = self.client.get(self.url).data["concentration"]

        self.assertEqual(
            [row["name"] for row in concentration["rows"]], ["Whale", "Middle", "Minnow"]
        )
        self.assertEqual(concentration["rows"][0]["share"], 60.0)
        self.assertEqual(concentration["rows"][1]["cumulative_share"], 90.0)
        self.assertEqual(concentration["top_three_share"], 100.0)

    def test_a_churned_customer_is_not_in_the_concentration_ranking(self):
        self._customer("Live", 100_000)
        self._customer("Left", 900_000, churn_date=self.today)

        concentration = self.client.get(self.url).data["concentration"]

        self.assertEqual([row["name"] for row in concentration["rows"]], ["Live"])
        self.assertEqual(concentration["total_arr"], 100_000.0)

    def test_the_tail_beyond_the_top_ten_is_folded_not_dropped(self):
        for index in range(13):
            self._customer(f"Co {index:02d}", 10_000 + index)

        concentration = self.client.get(self.url).data["concentration"]

        self.assertEqual(len(concentration["rows"]), portfolio.TOP_N)
        self.assertEqual(concentration["rest_count"], 3)
        self.assertGreater(concentration["rest_arr"], 0)
        self.assertEqual(concentration["counted"], 13)

    def test_an_unpriced_account_cannot_be_ranked_by_size(self):
        self._customer("Priced", 100_000)
        self._customer("Tokyo", 100_000, currency="JPY")

        data = self.client.get(self.url).data

        self.assertEqual([row["name"] for row in data["concentration"]["rows"]], ["Priced"])
        self.assertEqual(data["kpis"]["unpriced"], 1)

    # ── cohorts ──────────────────────────────────────────────────────

    def test_cohorts_group_by_join_year_and_report_retention(self):
        self._customer("Stayed", joined_date=date(2023, 3, 1))
        self._customer("Left", joined_date=date(2023, 6, 1), churn_date=self.today)
        self._customer("Newer", joined_date=date(2024, 1, 1))

        cohorts = self.client.get(self.url).data["cohorts"]

        rows = {row["year"]: row for row in cohorts["rows"]}
        self.assertEqual(rows[2023]["joined"], 2)
        self.assertEqual(rows[2023]["retained"], 1)
        self.assertEqual(rows[2023]["retention"], 50.0)
        self.assertEqual(rows[2024]["retention"], 100.0)

    def test_a_customer_with_no_join_date_is_counted_apart(self):
        """Dropping them into the earliest cohort would make it look larger and
        its retention worse."""
        self._customer("Dateless")
        self._customer("Dated", joined_date=date(2024, 1, 1))

        cohorts = self.client.get(self.url).data["cohorts"]

        self.assertEqual(cohorts["undated"], 1)
        self.assertEqual(sum(row["joined"] for row in cohorts["rows"]), 1)

    # ── churn reasons ────────────────────────────────────────────────

    def test_two_customers_leaving_for_the_same_reason_are_one_row(self):
        """The free-text field this replaced could not do it: "Budget cuts",
        "budget CUTS " and "Budget Cut" were three rows, and folding could
        honestly merge only the first two."""
        self._customer("A", 10_000, churn_date=self.today, churn_reason=Reason.BUDGET)
        self._customer("B", 20_000, churn_date=self.today, churn_reason=Reason.BUDGET)

        rows = self._reasons()

        self.assertEqual(rows[Reason.BUDGET]["customers"], 2)
        self.assertEqual(rows[Reason.BUDGET]["arr"], 30_000.0)

    def test_every_reason_on_the_list_gets_a_row_even_at_zero(self):
        """Impossible on free text, where an absent string and a reason nobody
        thought to type look identical. "Nothing lost to a missing capability"
        is a finding about the product."""
        self._customer("A", 10_000, churn_date=self.today, churn_reason=Reason.BUDGET)

        rows = self._reasons()

        self.assertEqual(len(rows), len(Reason.choices))
        self.assertEqual(rows[Reason.PRODUCT_GAP]["customers"], 0)
        self.assertEqual(rows[Reason.PRODUCT_GAP]["arr"], 0.0)

    def test_the_row_carries_the_label_as_well_as_the_stored_value(self):
        # So no screen keeps its own copy of the taxonomy.
        self._customer("A", 10_000, churn_date=self.today, churn_reason=Reason.CHAMPION_LEFT)

        self.assertEqual(self._reasons()[Reason.CHAMPION_LEFT]["reason"], "Champion left")

    def test_an_unrecorded_reason_is_its_own_row_not_folded_into_other(self):
        """ "We don't know" is a gap in the CRM; "Other" is a CSM saying none of
        the eleven fit. Counting the first as the second would hide the gap."""
        self._customer("Silent", 10_000, churn_date=self.today, churn_reason="")

        rows = self._reasons()

        self.assertEqual(rows[""]["reason"], portfolio.NO_REASON)
        self.assertEqual(rows[""]["customers"], 1)
        self.assertEqual(rows[Reason.OTHER]["customers"], 0)

    def test_an_unrecorded_reason_earns_no_row_when_it_never_happened(self):
        # Unlike the eleven real reasons, which are always listed.
        self._customer("A", 10_000, churn_date=self.today, churn_reason=Reason.PRICE)

        self.assertNotIn("", self._reasons())

    def test_reasons_are_ranked_by_the_money_that_left(self):
        self._customer("Small but common", 1_000, churn_date=self.today, churn_reason=Reason.PRICE)
        self._customer("Also price", 1_000, churn_date=self.today, churn_reason=Reason.PRICE)
        self._customer("One big one", 500_000, churn_date=self.today, churn_reason=Reason.ACQUIRED)

        self.assertEqual(
            self.client.get(self.url).data["churn_reasons"][0]["reason"], "Acquired or merged"
        )

    def test_a_reason_the_list_no_longer_has_is_counted_as_unrecorded(self):
        """A value left behind by an older release is not silently dropped —
        every churned customer has to appear in the reason counts, or they
        stop adding up to the churn figure above them."""
        customer = self._customer("Legacy", 10_000, churn_date=self.today)
        Customer.objects.filter(pk=customer.pk).update(churn_reason="retired_value")

        rows = self._reasons()

        self.assertEqual(rows[""]["customers"], 1)
        self.assertEqual(
            sum(row["customers"] for row in rows.values()),
            self.client.get(self.url).data["kpis"]["churned"],
        )

    # ── composition ──────────────────────────────────────────────────

    def test_segments_report_both_counts_and_money(self):
        # The two tell opposite stories: the smallest band is usually the most
        # accounts and the least money.
        self._customer("Tiny", 10_000)
        self._customer("Tiny two", 12_000)
        self._customer("Large", 400_000)

        rows = {row["key"]: row for row in self.client.get(self.url).data["segments"]["rows"]}

        self.assertEqual(rows["under_25k"]["customers"], 2)
        self.assertEqual(rows["under_25k"]["arr"], 22_000.0)
        self.assertEqual(rows["over_100k"]["customers"], 1)
        self.assertEqual(rows["over_100k"]["arr"], 400_000.0)

    def test_every_size_band_is_present_even_when_empty(self):
        self._customer("Only one", 400_000)

        rows = self.client.get(self.url).data["segments"]["rows"]

        self.assertEqual(len(rows), len(segments.REVENUE_BRACKETS))

    def test_empty_lifecycle_stages_are_dropped(self):
        # Eight stages and most books use four; keeping the empties would be
        # four bars of nothing.
        self._customer("One", lifecycle_stage=Customer.LifecycleStage.LIVE)

        lifecycle = self.client.get(self.url).data["lifecycle"]

        self.assertEqual([row["key"] for row in lifecycle], ["live"])

    # ── filters ──────────────────────────────────────────────────────

    def test_filters_narrow_the_book(self):
        target = self._customer("Target", lifecycle_stage=Customer.LifecycleStage.CHURN)
        self._customer("Other")

        self.assertEqual(
            self.client.get(self.url, {"customer": target.id}).data["kpis"]["active"], 1
        )
        self.assertEqual(
            self.client.get(self.url, {"lifecycle": "churn"}).data["kpis"]["active"], 1
        )

    def test_filter_options_include_churned_customers(self):
        """Unlike every other dashboard's options — this screen is about them,
        so being unable to filter to one would be strange."""
        self._customer("Left", churn_date=self.today)

        options = self.client.get(self.url).data["filters"]

        self.assertIn("Left", [row["name"] for row in options["customers"]])
