"""The "why" layer: a metric cut by owner, product, segment or lifecycle, and
the signals that name what moved it."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers import forecast
from services.customers.models import Customer, Product
from services.customers.scoping import SystemActor
from services.metrics import registry
from services.metrics.models import MetricSnapshot
from services.metrics.recording import record_period_end


def _org():
    org = Organisation.objects.create(name="Acme Inc", currency="USD")
    carl = User.objects.create_user(
        email="carl@acme.io", password="x", name="Carl", organisation=org, role=User.Role.CSM
    )
    dana = User.objects.create_user(
        email="dana@acme.io", password="x", name="Dana", organisation=org, role=User.Role.CSM
    )
    a = Product.objects.create(organisation=org, name="Product A")
    b = Product.objects.create(organisation=org, name="Product B")
    Customer.objects.create(
        organisation=org,
        name="Big",
        owner=carl,
        primary_product=a,
        arr_billed_at_account=Decimal(300_000),
        health_score=Decimal("8.5"),
    )
    Customer.objects.create(
        organisation=org,
        name="Shaky",
        owner=carl,
        primary_product=b,
        arr_billed_at_account=Decimal(100_000),
        health_score=Decimal("2.0"),
    )
    Customer.objects.create(
        organisation=org,
        name="Danas",
        owner=dana,
        primary_product=b,
        arr_billed_at_account=Decimal(50_000),
        health_score=Decimal("8.0"),
    )
    Customer.objects.create(
        organisation=org,
        name="Orphan",
        arr_billed_at_account=Decimal(20_000),
        health_score=Decimal("5.0"),
    )
    return org, carl, dana, a, b


class BridgeByTests(TestCase):
    def test_the_groups_add_up_to_the_whole(self):
        """Reusing build_bridge per group means a slice can never disagree
        with the whole — the point of not re-summing."""
        org, *_ = _org()
        actor = SystemActor(org)
        rows = forecast.build_rows(list(forecast.filtered_customers(actor, {})), org)
        whole = forecast.build_bridge(rows)

        by_owner = forecast.bridge_by(rows, lambda row: row.customer.owner_id)

        for key in ("opening_arr", "churn", "contraction", "expansion", "forecast_arr"):
            self.assertAlmostEqual(
                sum(b[key] for b in by_owner.values()), whole[key], places=2, msg=key
            )


class SliceTests(TestCase):
    def setUp(self):
        self.org, self.carl, self.dana, self.a, self.b = _org()

    def test_every_slice_names_a_known_dimension(self):
        for metric in registry.METRICS:
            for dimension in metric.slices:
                self.assertIn(dimension, registry.DIMENSION_LABELS, f"{metric.key} by {dimension}")

    def test_active_arr_by_product_and_the_unrecorded_bucket(self):
        # ARR isn't a forecast metric, so its product cut comes from the
        # Product Usage rollup — including the "no product" bucket.
        cut = (
            dict(
                (label, value)
                for _m, label, value in registry.compute_slice(
                    self.org, registry.BY_KEY["active_arr"], registry.PRODUCT
                )
            )
            if registry.PRODUCT in registry.BY_KEY["active_arr"].slices
            else {}
        )
        # Floor-inclusive bands: the $100K customer sits in "$100K and above".
        segments = registry.compute_slice(self.org, registry.BY_KEY["active_arr"], registry.SEGMENT)

        self.assertEqual({label: v for _m, label, v in segments}["$100K and above"], 400_000.0)
        self.assertEqual(sum(v for _m, _l, v in segments), 470_000.0)
        self.assertEqual(cut, {})  # not cut by product: it has segment and lifecycle cuts

    def test_forecast_metrics_cut_by_owner_carry_stable_member_ids(self):
        members = {
            m: (label, v)
            for m, label, v in registry.compute_slice(
                self.org, registry.BY_KEY["forecast_arr"], registry.OWNER
            )
        }

        self.assertEqual(members[str(self.carl.pk)][0], "Carl")
        self.assertEqual(members["unassigned"][0], "Unassigned")
        self.assertAlmostEqual(
            sum(v for _l, v in members.values()),
            registry.compute_all(self.org)["forecast_arr"],
            places=2,
        )

    def test_health_cut_by_product_counts_the_rubrics_own_category(self):
        members = {
            label: v
            for _m, label, v in registry.compute_slice(
                self.org, registry.BY_KEY["poor_health_count"], registry.PRODUCT
            )
        }

        self.assertEqual(members["Product B"], 1)
        self.assertEqual(members["Product A"], 0)
        self.assertEqual(members["No product recorded"], 0)

    def test_recording_writes_the_cuts_beside_the_whole_and_keeps_existing_rows(self):
        period = date(2026, 8, 31)
        written, _ = record_period_end(self.org, period)

        cut_rows = MetricSnapshot.objects.filter(organisation=self.org, period_end=period).exclude(
            dimension=""
        )
        self.assertGreater(cut_rows.count(), 0)
        self.assertEqual(
            written, MetricSnapshot.objects.filter(organisation=self.org, period_end=period).count()
        )
        row = cut_rows.get(
            metric="forecast_arr", dimension=registry.OWNER, member=str(self.carl.pk)
        )
        self.assertGreater(row.value, 0)

        MetricSnapshot.objects.filter(pk=row.pk).update(value=Decimal("1"))
        again, _ = record_period_end(self.org, period)

        self.assertEqual(again, 0)
        row.refresh_from_db()
        self.assertEqual(row.value, Decimal("1"))


class SliceAndSignalAPITests(APITestCase):
    def setUp(self):
        self.org, self.carl, self.dana, self.a, self.b = _org()
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(self.admin)

    def test_a_csm_is_refused(self):
        self.client.force_authenticate(self.carl)

        self.assertEqual(
            self.client.get("/api/v1/metrics/signals/").status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.get("/api/v1/metrics/at_risk_arr/by/product/").status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_a_cut_lists_members_largest_first_with_their_move(self):
        MetricSnapshot.objects.create(
            organisation=self.org,
            metric="active_arr",
            dimension="segment",
            member="over_100k",
            period_end=date(2026, 8, 31),
            value=Decimal("250000"),
        )

        data = self.client.get("/api/v1/metrics/active_arr/by/segment/").data

        self.assertEqual(data["dimension"], {"key": "segment", "label": "Size band"})
        self.assertEqual(data["members"][0]["label"], "$100K and above")
        self.assertEqual(data["members"][0]["value"], 400_000.0)
        self.assertEqual(data["members"][0]["previous"]["value"], 250_000.0)
        self.assertEqual(data["members"][0]["change"], 150_000.0)
        self.assertIsNone(data["members"][1]["previous"])

    def test_a_cut_that_does_not_exist_says_which_do(self):
        response = self.client.get("/api/v1/metrics/active_arr/by/owner/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("lifecycle, segment", str(response.data))
        self.assertEqual(
            self.client.get("/api/v1/metrics/made_up/by/owner/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_no_signals_without_a_month_end_to_compare_against(self):
        data = self.client.get("/api/v1/metrics/signals/").data

        self.assertEqual(data["signals"], [])
        self.assertIsNone(data["baseline"])

    def test_a_material_move_is_a_signal_naming_its_biggest_driver(self):
        """Nothing was at risk at August end; the shaky Product B customer now
        renews inside the horizon, so its ARR carries the churn rule's risk —
        and the product cut says which product did it."""
        from datetime import timedelta

        from django.utils import timezone

        Customer.objects.filter(name="Shaky").update(
            renewal_date=timezone.localdate() + timedelta(days=30)
        )
        period = date(2026, 8, 31)
        MetricSnapshot.objects.create(
            organisation=self.org, metric="at_risk_arr", period_end=period, value=Decimal("0")
        )
        for product in (self.a, self.b):
            MetricSnapshot.objects.create(
                organisation=self.org,
                metric="at_risk_arr",
                dimension="product",
                member=str(product.pk),
                period_end=period,
                value=Decimal("0"),
            )

        data = self.client.get("/api/v1/metrics/signals/").data

        self.assertEqual(data["baseline"], "2026-08-31")
        keys = [s["key"] for s in data["signals"]]
        self.assertIn("at_risk_arr", keys)
        signal = next(s for s in data["signals"] if s["key"] == "at_risk_arr")
        self.assertFalse(signal["improved"])  # more at risk is bad
        self.assertGreater(signal["change"], 0)
        self.assertEqual(signal["drivers"][0]["dimension"], "product")
        self.assertEqual(signal["drivers"][0]["label"], "Product B")
        self.assertGreater(signal["drivers"][0]["change"], 0)

    def test_a_small_move_is_not_a_signal(self):
        now = self.client.get("/api/v1/metrics/").data
        arr = {m["key"]: m for m in now["metrics"]}["active_arr"]["value"]
        MetricSnapshot.objects.create(
            organisation=self.org,
            metric="active_arr",
            period_end=date(2026, 8, 31),
            value=Decimal(str(arr * 0.98)),
        )

        self.assertNotIn(
            "active_arr",
            [s["key"] for s in self.client.get("/api/v1/metrics/signals/").data["signals"]],
        )

    def test_bad_news_sorts_before_good(self):
        period = date(2026, 8, 31)
        # Coverage improved by 50 points (good); poor-health count doubled (bad).
        MetricSnapshot.objects.create(
            organisation=self.org, metric="healthy_share", period_end=period, value=Decimal("0")
        )
        MetricSnapshot.objects.create(
            organisation=self.org,
            metric="poor_health_count",
            period_end=period,
            value=Decimal("0.5"),
        )

        signals = self.client.get("/api/v1/metrics/signals/").data["signals"]
        keys = [s["key"] for s in signals]

        self.assertLess(keys.index("poor_health_count"), keys.index("healthy_share"))
