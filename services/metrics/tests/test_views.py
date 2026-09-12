from datetime import date
from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Customer
from services.metrics.models import MetricSnapshot
from services.metrics.registry import METRICS


class MetricAPITests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        Customer.objects.create(
            organisation=self.org,
            name="Globex",
            owner=self.csm,
            arr_billed_at_account=Decimal(120_000),
        )
        self.client.force_authenticate(self.admin)

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(
            self.client.get("/api/v1/metrics/").status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_a_csm_cannot_read_the_organisations_numbers(self):
        """Every figure here is whole-org. A CSM's book is scoped to their own
        customers, and this endpoint would hand them the company's ARR."""
        self.client.force_authenticate(self.csm)

        self.assertEqual(self.client.get("/api/v1/metrics/").status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.get("/api/v1/metrics/active_arr/history/").status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_lists_every_metric_with_its_value_now(self):
        data = self.client.get("/api/v1/metrics/").data

        self.assertEqual([m["key"] for m in data["metrics"]], [m.key for m in METRICS])
        by_key = {m["key"]: m for m in data["metrics"]}
        self.assertEqual(by_key["active_arr"]["value"], 120_000.0)
        self.assertEqual(by_key["active_arr"]["unit"], "money")
        self.assertEqual(by_key["active_arr"]["better"], "up")
        self.assertEqual(by_key["active_arr"]["dimensions"], ["lifecycle", "segment"])
        self.assertEqual(by_key["open_tickets"]["dimensions"], [])
        self.assertEqual(data["currency"], "USD")

    def test_carries_the_last_month_end_and_the_change_since(self):
        MetricSnapshot.objects.create(
            organisation=self.org,
            metric="active_arr",
            period_end=date(2026, 7, 31),
            value=Decimal("100000"),
        )
        MetricSnapshot.objects.create(
            organisation=self.org,
            metric="active_arr",
            period_end=date(2026, 8, 31),
            value=Decimal("110000"),
        )

        row = {m["key"]: m for m in self.client.get("/api/v1/metrics/").data["metrics"]}[
            "active_arr"
        ]

        self.assertEqual(row["previous"], {"period_end": "2026-08-31", "value": 110_000.0})
        self.assertEqual(row["change"], 10_000.0)

    def test_no_history_means_no_previous_and_no_change(self):
        row = {m["key"]: m for m in self.client.get("/api/v1/metrics/").data["metrics"]}[
            "active_arr"
        ]

        self.assertIsNone(row["previous"])
        self.assertIsNone(row["change"])

    def test_a_change_from_unmeasured_is_not_a_change(self):
        MetricSnapshot.objects.create(
            organisation=self.org, metric="nrr", period_end=date(2026, 8, 31), value=None
        )

        row = {m["key"]: m for m in self.client.get("/api/v1/metrics/").data["metrics"]}["nrr"]

        self.assertIsNone(row["change"])

    def test_history_is_oldest_first_and_only_this_organisation(self):
        other = Organisation.objects.create(name="Other Inc", currency="USD")
        for org, month, value in [
            (self.org, 8, "110000"),
            (self.org, 7, "100000"),
            (other, 8, "999999"),
        ]:
            MetricSnapshot.objects.create(
                organisation=org,
                metric="active_arr",
                period_end=date(2026, month, 31),
                value=Decimal(value),
            )

        data = self.client.get("/api/v1/metrics/active_arr/history/").data

        self.assertEqual(data["metric"]["key"], "active_arr")
        self.assertEqual(
            data["points"],
            [
                {"period_end": "2026-07-31", "value": 100_000.0},
                {"period_end": "2026-08-31", "value": 110_000.0},
            ],
        )

    def test_an_unknown_metric_is_a_404(self):
        self.assertEqual(
            self.client.get("/api/v1/metrics/made_up/history/").status_code,
            status.HTTP_404_NOT_FOUND,
        )
