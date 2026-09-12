"""Product Usage: one row per product, compared against each other.

The tests are mostly about *attribution* — which customers a product's figures
speak for — because that is the only thing this screen can get badly wrong.
`primary_product` names one product per customer, so every number here is "the
customers this product leads", and a reader who takes it for "revenue split
across products" will double-count nothing and under-count plenty.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers import product_usage
from services.customers.models import Customer, Ticket


class AverageTests(TestCase):
    def test_missing_scores_are_absent_not_zero(self):
        # A product nobody surveyed has no score. Zero would make it the
        # worst-rated product on the screen.
        self.assertIsNone(product_usage._average([None, None]))
        self.assertEqual(product_usage._average([80, None, 90]), 85.0)


class ProductUsageTests(APITestCase):
    url = "/api/v1/customers/products/"

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

    def _customer(self, name, product="Product A", arr=100_000, **overrides):
        return Customer.objects.create(
            organisation=self.org,
            name=name,
            owner=overrides.pop("owner", self.csm),
            primary_product=product,
            arr_billed_at_account=Decimal(arr),
            **overrides,
        )

    def _ticket(self, customer, number, status_value):
        return Ticket.objects.create(
            customer=customer,
            ticket_number=f"TKT-{number}",
            title="Broken",
            assignee_name="Support",
            status=status_value,
            priority=Ticket.Priority.LOW,
            opened_at=self.today,
        )

    def _rows(self, **params):
        return {row["product"]: row for row in self.client.get(self.url, params).data["rows"]}

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_another_owners_book_is_invisible(self):
        self._customer("Mine", "Product A")
        self._customer("Theirs", "Product B", owner=self.other)

        self.assertEqual(list(self._rows()), ["Product A"])

    # ── attribution: the thing this screen must not hide ─────────────

    def test_a_customer_counts_once_under_their_primary_product_only(self):
        """`additional_products_count` records *how many* other products, never
        which, so there is nothing to attribute them to. The response says so
        in `attribution` rather than leaving the reader to assume a split."""
        self._customer("Multi", "Product A", 90_000, additional_products_count=3)

        data = self.client.get(self.url).data

        self.assertEqual(data["rows"][0]["customers"], 1)
        self.assertEqual(data["rows"][0]["arr"], 90_000.0)
        self.assertEqual(data["attribution"]["basis"], "primary_product")
        self.assertIn("not revenue split across products", data["attribution"]["note"])

    def test_product_shares_add_up_to_the_whole_book(self):
        self._customer("One", "Product A", 75_000)
        self._customer("Two", "Product B", 25_000)

        rows = self._rows()

        self.assertEqual(rows["Product A"]["share"], 75.0)
        self.assertEqual(rows["Product B"]["share"], 25.0)

    def test_customers_with_no_product_are_their_own_row_not_dropped(self):
        """ "Nobody recorded what they bought" is a finding about the CRM.
        Dropping them would make the shares add up to less than 100% with no
        explanation on the screen."""
        self._customer("Known", "Product A", 60_000)
        self._customer("Unknown", "", 40_000)

        rows = self._rows()

        self.assertIn(product_usage.NO_PRODUCT, rows)
        self.assertEqual(rows[product_usage.NO_PRODUCT]["customers"], 1)
        self.assertEqual(rows[product_usage.NO_PRODUCT]["spellings"], 0)
        self.assertEqual(sum(row["share"] for row in rows.values()), 100.0)

    # ── free text, the same problem as churn_reason ───────────────────

    def test_spellings_of_one_product_fold_into_one_row(self):
        self._customer("A", "Product A", 10_000)
        self._customer("B", "  product a ", 20_000)

        rows = self.client.get(self.url).data["rows"]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["customers"], 2)
        self.assertEqual(rows[0]["arr"], 30_000.0)
        # Reported so a reader can see the folding happen, and argue for
        # choices on the field.
        self.assertEqual(rows[0]["spellings"], 2)

    def test_names_only_a_human_would_merge_stay_separate(self):
        # "Product A" and "Product A Pro" differ by more than case. Nothing
        # here can merge them honestly.
        self._customer("A", "Product A")
        self._customer("B", "Product A Pro")

        self.assertEqual(len(self.client.get(self.url).data["rows"]), 2)

    # ── churn is half the point ──────────────────────────────────────

    def test_churned_customers_are_counted_here_unlike_the_working_dashboards(self):
        """A product whose customers all left would otherwise read as a product
        with no problems."""
        self._customer("Live", "Product A", 100_000)
        self._customer("Left", "Product A", 50_000, churn_date=self.today - timedelta(days=20))

        row = self._rows()["Product A"]

        # Active figures exclude the leaver...
        self.assertEqual(row["customers"], 1)
        self.assertEqual(row["arr"], 100_000.0)
        # ...and the churn figures are about them.
        self.assertEqual(row["churned"], 1)
        self.assertEqual(row["churned_arr"], 50_000.0)
        self.assertEqual(row["churn_rate"], 50.0)

    def test_a_product_with_nothing_left_still_gets_a_row(self):
        """The most important row on the screen when it happens, and an ARR sort
        would otherwise bury it or a customer count drop it."""
        self._customer("Gone", "Dead Product", 80_000, churn_date=self.today)

        row = self._rows()["Dead Product"]

        self.assertEqual(row["customers"], 0)
        self.assertEqual(row["arr"], 0.0)
        self.assertEqual(row["churn_rate"], 100.0)
        self.assertIsNone(row["healthy_share"])

    def test_churn_rate_is_over_everyone_the_product_ever_led(self):
        for index in range(3):
            self._customer(f"Live {index}", "Product A")
        self._customer("Left", "Product A", churn_date=self.today)

        self.assertEqual(self._rows()["Product A"]["churn_rate"], 25.0)

    def test_archiving_is_not_churn_and_not_active_either(self):
        self._customer("Live", "Product A")
        self._customer("Filed", "Product A", is_archived=True)

        row = self._rows()["Product A"]

        self.assertEqual(row["customers"], 1)
        self.assertEqual(row["churned"], 0)

    # ── the measures ─────────────────────────────────────────────────

    def test_utilisation_is_seats_over_seats_not_a_mean_of_percentages(self):
        """The same rule the Usage Overview uses, so the two screens cannot
        report different utilisation for the same accounts. A mean of
        percentages here would read 55.0."""
        self._customer("Big", "Product A", total_contracted_seats=1000, total_active_seats=100)
        self._customer("Small", "Product A", total_contracted_seats=10, total_active_seats=10)

        self.assertEqual(self._rows()["Product A"]["utilisation"], 10.9)

    def test_unmeasured_seats_are_no_utilisation_rather_than_zero(self):
        self._customer("Unmetered", "Product A", total_contracted_seats=0)

        self.assertIsNone(self._rows()["Product A"]["utilisation"])

    def test_satisfaction_averages_only_the_customers_who_answered(self):
        self._customer("Answered", "Product A", ces_percentage=Decimal("80"), nps_score=50)
        self._customer("Silent", "Product A")

        row = self._rows()["Product A"]

        self.assertEqual(row["ces"], 80.0)
        self.assertEqual(row["nps"], 50.0)

    def test_tickets_on_a_division_count_toward_the_product(self):
        # Support load on a subsidiary is support load on the product.
        customer = self._customer("Parent", "Product A")
        self._ticket(customer, 1, Ticket.Status.OPEN)
        self._ticket(customer, 2, Ticket.Status.CLOSED)

        row = self._rows()["Product A"]

        self.assertEqual(row["open_tickets"], 1)
        self.assertEqual(row["tickets_per_customer"], 1.0)

    def test_health_mix_is_reported_in_full_not_just_a_score(self):
        self._customer("Good", "Product A", health_score=Decimal("8.5"))
        self._customer("Bad", "Product A", health_score=Decimal("3.0"))

        row = self._rows()["Product A"]

        self.assertEqual(row["health"][Customer.HealthCategory.GOOD], 1)
        self.assertEqual(row["health"][Customer.HealthCategory.POOR], 1)
        self.assertEqual(row["healthy_share"], 50.0)

    def test_an_unpriced_customer_is_named_not_silently_worth_nothing(self):
        self._customer("Priced", "Product A", 100_000)
        self._customer("Tokyo", "Product A", 100_000, currency="JPY")

        row = self._rows()["Product A"]

        self.assertEqual(row["customers"], 2)
        self.assertEqual(row["arr"], 100_000.0)
        self.assertEqual(row["unpriced"], 1)

    # ── the headline calls ───────────────────────────────────────────

    def test_largest_is_by_the_arr_the_product_leads(self):
        self._customer("A", "Product A", 300_000)
        self._customer("B", "Product B", 100_000)

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["largest"]["product"], "Product A")
        self.assertEqual(kpis["largest"]["share"], 75.0)

    def test_weakest_is_the_money_at_stake_not_the_worst_percentage(self):
        """A percentage put one unhappy customer on a small product above three
        on a product with five times the revenue. "Which do we fix first" is
        answered in money."""
        self._customer("Tiny and unhappy", "Small Product", 10_000, health_score=Decimal("2.0"))
        for index in range(3):
            self._customer(f"Unhappy {index}", "Big Product", 60_000, health_score=Decimal("4.0"))

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["weakest"]["product"], "Big Product")
        self.assertEqual(kpis["weakest"]["unhealthy_arr"], 180_000.0)
        self.assertEqual(kpis["weakest"]["healthy_share"], 0.0)

    def test_a_healthy_book_has_no_weakest_product(self):
        self._customer("Happy", "Product A", health_score=Decimal("9.0"))

        self.assertIsNone(self.client.get(self.url).data["kpis"]["weakest"])

    def test_worst_churn_is_by_the_arr_that_left(self):
        self._customer("Many small", "Product A", 1_000, churn_date=self.today)
        self._customer("Also small", "Product A", 1_000, churn_date=self.today)
        self._customer("One big", "Product B", 400_000, churn_date=self.today)

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["worst_churn"]["product"], "Product B")
        self.assertEqual(kpis["worst_churn"]["churned_arr"], 400_000.0)

    def test_an_empty_book_reports_nothing_rather_than_a_flattering_figure(self):
        data = self.client.get(self.url).data

        self.assertEqual(data["rows"], [])
        self.assertIsNone(data["kpis"]["largest"])
        self.assertIsNone(data["kpis"]["weakest"])
        self.assertIsNone(data["kpis"]["worst_churn"])

    # ── filters ──────────────────────────────────────────────────────

    def test_filters_narrow_the_book(self):
        self._customer("Target", "Product A", lifecycle_stage=Customer.LifecycleStage.LIVE)
        self._customer("Other", "Product B", owner=self.other)

        self.assertEqual(
            list(self._rows(lifecycle=Customer.LifecycleStage.LIVE)),
            ["Product A"],
        )
        self.assertEqual(list(self._rows(owner=self.csm.id)), ["Product A"])

    def test_the_product_filter_matches_whatever_spelling_was_typed(self):
        self._customer("A", "Product A")
        self._customer("B", "Product B")

        self.assertEqual(list(self._rows(product="product a")), ["Product A"])

    def test_customers_with_no_product_can_be_filtered_to(self):
        self._customer("Known", "Product A")
        self._customer("Unknown", "")

        self.assertEqual(
            list(self._rows(product=product_usage.NO_PRODUCT)), [product_usage.NO_PRODUCT]
        )

    def test_the_product_dropdown_is_built_from_the_book_because_there_is_no_product_table(self):
        self._customer("A", "Product B")
        self._customer("B", "Product A")
        self._customer("C", "")

        options = self.client.get(self.url).data["filters"]["products"]

        self.assertEqual(
            [row["name"] for row in options], ["Product A", "Product B", product_usage.NO_PRODUCT]
        )

    def test_churned_customers_keep_their_product_in_the_dropdown(self):
        self._customer("Gone", "Dead Product", churn_date=self.today)

        options = self.client.get(self.url).data["filters"]["products"]

        self.assertIn("Dead Product", [row["name"] for row in options])
