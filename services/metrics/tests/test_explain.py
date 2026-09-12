"""Why is this number where it is — grounded, stored, gated."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation
from services.customers.models import Customer
from services.metrics import explain
from services.metrics.models import Explanation, MetricSnapshot

from .test_brief import _org

PATH = "services.metrics.explain.get_completion"
ANSWER = (
    '{"text": "ARR at risk is USD 17,400, all of it on Product B, where Shaky carries the '
    'downside with a health score in the poor band.", '
    '"evidence": ["Product B: USD 17,400", "Shaky (owner Carl): downside USD 17,400"]}'
)


class EvidenceAndPromptTests(TestCase):
    def setUp(self):
        self.org, self.admin, self.csm = _org()
        # A renewal inside the horizon, so Shaky's poor health becomes downside.
        Customer.objects.filter(organisation=self.org, name="Shaky").update(
            renewal_date=timezone.localdate() + timedelta(days=30)
        )

    def test_the_prompt_carries_the_definition_the_move_and_the_cuts_with_their_own_moves(self):
        MetricSnapshot.objects.create(
            organisation=self.org,
            metric="at_risk_arr",
            period_end=date(2026, 8, 31),
            value=Decimal("10000"),
        )
        MetricSnapshot.objects.create(
            organisation=self.org,
            metric="at_risk_arr",
            dimension="owner",
            member=str(self.csm.id),
            period_end=date(2026, 8, 31),
            value=Decimal("10000"),
        )

        evidence = explain.build_evidence(self.org, "at_risk_arr")
        prompt = explain.build_prompt(evidence)

        self.assertTrue(prompt.startswith("METRIC: ARR at risk [at_risk_arr]"))
        self.assertIn("lower is better", prompt)
        self.assertIn("At the 2026-08-31 month-end: USD 10,000", prompt)
        self.assertIn("BY OWNER:", prompt)
        self.assertIn("- Carl:", prompt)
        self.assertIn("(was USD 10,000)", prompt)
        self.assertIn("ACCOUNTS CARRYING THE DOWNSIDE:", prompt)
        self.assertIn("- Shaky (owner Carl)", prompt)
        self.assertNotIn("Big (owner", prompt)

    def test_without_a_month_end_the_prompt_asks_for_the_level(self):
        prompt = explain.build_prompt(explain.build_evidence(self.org, "active_arr"))
        self.assertIn("No month-end recorded yet", prompt)
        self.assertNotIn("(was", prompt)

    def test_an_unknown_key_and_an_empty_book_are_refused(self):
        with self.assertRaises(explain.UnknownMetric):
            explain.build_evidence(self.org, "nope")
        empty = Organisation.objects.create(name="New Inc", currency="USD")
        with self.assertRaises(explain.NothingToExplain):
            explain.build_evidence(empty, "active_arr")


class ExplainTests(TestCase):
    def setUp(self):
        self.org, self.admin, self.csm = _org()

    def test_the_explanation_is_stored_with_the_figures_it_described(self):
        with patch(PATH, return_value=ANSWER) as call:
            row = explain.explain(self.org, "at_risk_arr", generated_by=self.admin)

        self.assertEqual(call.call_args.kwargs["purpose"], "explain")
        self.assertEqual(row.metric, "at_risk_arr")
        self.assertTrue(row.text.startswith("ARR at risk is USD 17,400"))
        self.assertEqual(len(row.evidence), 2)
        self.assertEqual(row.inputs["metric"]["label"], "ARR at risk")
        self.assertIsNone(row.baseline)
        self.assertEqual(row.generated_by, self.admin)

    def test_prose_around_the_object_is_tolerated_and_an_empty_text_is_not(self):
        with patch(PATH, return_value="Sure — here it is:\n" + ANSWER + "\nHope that helps."):
            self.assertEqual(len(explain.explain(self.org, "at_risk_arr").evidence), 2)
        with patch(PATH, return_value="{" + ANSWER + "}"):
            self.assertEqual(len(explain.explain(self.org, "at_risk_arr").evidence), 2)
        with patch(PATH, return_value='{"text": "", "evidence": []}'):
            with self.assertRaises(ValueError):
                explain.explain(self.org, "at_risk_arr")


class ViewTests(APITestCase):
    def setUp(self):
        self.org, self.admin, self.csm = _org()

    def test_read_is_null_until_written_then_returns_the_latest(self):
        self.client.force_authenticate(self.admin)
        self.assertIsNone(
            self.client.get("/api/v1/metrics/at_risk_arr/explanation/").data["explanation"]
        )

        with patch(PATH, return_value=ANSWER):
            written = self.client.post("/api/v1/metrics/at_risk_arr/explain/")
        self.assertEqual(written.status_code, status.HTTP_201_CREATED)
        self.assertEqual(written.data["explanation"]["metric_label"], "ARR at risk")
        self.assertEqual(written.data["explanation"]["generated_by"], "Alice")

        read = self.client.get("/api/v1/metrics/at_risk_arr/explanation/").data["explanation"]
        self.assertEqual(read["id"], written.data["explanation"]["id"])
        self.assertEqual(Explanation.objects.count(), 1)

    def test_an_unknown_key_is_404_and_a_csm_is_403(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(
            self.client.get("/api/v1/metrics/nope/explanation/").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        with patch(PATH) as call:
            self.assertEqual(
                self.client.post("/api/v1/metrics/nope/explain/").status_code,
                status.HTTP_404_NOT_FOUND,
            )
        call.assert_not_called()
        self.client.force_authenticate(self.csm)
        self.assertEqual(
            self.client.get("/api/v1/metrics/at_risk_arr/explanation/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
